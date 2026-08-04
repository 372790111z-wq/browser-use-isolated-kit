"""Shared, leased tab workspace for the local isolated Chrome."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import time

from browser_harness import _ipc
import browser_harness.helpers as _h


# Agent helpers are copied back onto browser_harness.helpers after this module
# loads. Preserve core references now so the public overrides below never call
# themselves recursively through `_h`.
_BASE_GOTO_URL = _h.goto_url


_THREAD_ID = os.environ.get("CODEX_THREAD_ID") or "shared"
_GENERATION = os.environ.get("BU_BROWSER_GENERATION") or "unknown"
_POOL_SIZE = int(os.environ.get("BU_TAB_POOL_SIZE") or "12")
_HARD_LIMIT = int(os.environ.get("BU_TAB_POOL_HARD_LIMIT") or str(_POOL_SIZE * 2))
_WORKSPACE = Path(os.environ.get("BH_AGENT_WORKSPACE") or ".")
_STATE_DIR = _WORKSPACE / "tab-pool"
_STATE_PATH = _STATE_DIR / "state.json"
_LOCK_PATH = _STATE_DIR / "state.lock"
_POOL_URL_RE = re.compile(r"^about:blank#codex-pool-(\d+)$")


def _new_state():
    return {
        "version": 4,
        "generation": _GENERATION,
        "pool_size": _POOL_SIZE,
        "hard_limit": _HARD_LIMIT,
        "slots": {},
        "assignments": {},
        "archive": [],
        "updated_at": time.time(),
    }


def _read_state():
    try:
        state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _new_state()
    if state.get("generation") != _GENERATION:
        return _new_state()
    if state.get("version") in (2, 3):
        state["version"] = 4
        state["hard_limit"] = _HARD_LIMIT
        state.setdefault("archive", [])
        for assignment in state.get("assignments", {}).values():
            assignment.setdefault("completed_slots", {})
        return state
    if state.get("version") != 4:
        return _new_state()
    return state


def _write_state(state):
    state["updated_at"] = time.time()
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=_STATE_DIR,
        prefix="state.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(_STATE_PATH)


@contextmanager
def _locked_state():
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = _read_state()
        try:
            yield state
            _write_state(state)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _refresh_pool(state):
    tabs = [
        {
            "targetId": item.get("targetId"),
            "target_id": item.get("targetId"),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
        }
        for item in _h.cdp("Target.getTargets").get("targetInfos", [])
        if item.get("type") == "page" and item.get("targetId")
    ]
    targets = {
        tab.get("targetId") or tab.get("target_id"): tab
        for tab in tabs
        if tab.get("targetId") or tab.get("target_id")
    }

    slots = state.setdefault("slots", {})
    for slot, target_id in list(slots.items()):
        if target_id not in targets:
            slots.pop(slot, None)

    for target_id, tab in targets.items():
        match = _POOL_URL_RE.match(str(tab.get("url") or ""))
        if not match:
            continue
        slot = int(match.group(1))
        if 1 <= slot <= _HARD_LIMIT:
            slots[str(slot)] = target_id

    # After a Chrome restart target ids change and restored real pages no longer
    # carry pool-marker URLs. Adopt every unmatched page into the shared
    # isolated workspace instead of losing it or silently navigating it away.
    tracked = set(slots.values())
    free_slots = [
        value for value in range(1, _HARD_LIMIT + 1) if str(value) not in slots
    ]
    for target_id in sorted(targets):
        if target_id in tracked or not free_slots:
            continue
        slot = free_slots.pop(0)
        slots[str(slot)] = target_id
        tracked.add(target_id)

    assignments = state.setdefault("assignments", {})
    for thread_id, assignment in list(assignments.items()):
        raw_slots = assignment.get("slots") or []
        valid_slots = []
        for raw_slot in raw_slots:
            slot = str(raw_slot)
            target_id = slots.get(slot)
            if target_id and target_id in targets:
                valid_slots.append(int(slot))
        valid_slots = sorted(set(valid_slots))
        if not valid_slots:
            assignments.pop(thread_id, None)
            continue

        assignment["slots"] = valid_slots
        current_slot = int(assignment.get("current_slot") or valid_slots[-1])
        if current_slot not in valid_slots:
            current_slot = valid_slots[-1]
        assignment["current_slot"] = current_slot

        raw_completed = assignment.get("completed_slots") or {}
        completed = {}
        for slot in valid_slots:
            value = raw_completed.get(str(slot), raw_completed.get(slot))
            try:
                completed_at = float(value)
            except (TypeError, ValueError):
                continue
            if completed_at > 0:
                completed[str(slot)] = completed_at
        assignment["completed_slots"] = completed

    return targets


def _fully_completed(assignment):
    slots = {str(slot) for slot in assignment.get("slots", [])}
    completed = set((assignment.get("completed_slots") or {}).keys())
    return bool(slots) and slots.issubset(completed)


def _stop_thread_daemon(thread_id):
    """Stop an old completed thread before transferring one of its targets."""
    if thread_id == _THREAD_ID:
        return True
    name = f"codex-isolated-{thread_id}"
    if not _ipc.ping(name, timeout=0.2):
        return True
    try:
        client, token = _ipc.connect(name, timeout=0.5)
        try:
            _ipc.request(client, token, {"meta": "shutdown"})
        finally:
            client.close()
    except (OSError, ValueError):
        return False
    for _ in range(20):
        if not _ipc.ping(name, timeout=0.05):
            return True
        time.sleep(0.05)
    return False


def _thread_is_active(thread_id):
    if thread_id == _THREAD_ID:
        return True
    return _ipc.ping(f"codex-isolated-{thread_id}", timeout=0.15)


def _archive_target(state, targets, thread_id, slot, reason):
    target_id = state["slots"].get(str(slot))
    tab = targets.get(target_id, {})
    state.setdefault("archive", []).append(
        {
            "target_id": target_id,
            "slot": int(slot),
            "owner_thread": thread_id,
            "title": tab.get("title", ""),
            "url": tab.get("url", ""),
            "reason": reason,
            "archived_at": time.time(),
        }
    )
    state["archive"] = state["archive"][-200:]


def _create_target(state):
    used = {int(slot) for slot in state["slots"]}
    slot = next((value for value in range(1, _HARD_LIMIT + 1) if value not in used), None)
    if slot is None:
        return None, None
    marker = f"about:blank#codex-pool-{slot:02d}"
    result = _h.cdp("Target.createTarget", url=marker, background=True)
    target_id = result.get("targetId")
    if not target_id:
        raise RuntimeError("Chrome did not create a background Browser Use tab")
    state["slots"][str(slot)] = target_id
    return str(slot), target_id


def _remove_assignment_slot(assignments, thread_id, slot):
    assignment = assignments.get(thread_id)
    if not assignment:
        return
    remaining = [item for item in assignment.get("slots", []) if int(item) != slot]
    completed = assignment.setdefault("completed_slots", {})
    completed.pop(str(slot), None)
    if not remaining:
        assignments.pop(thread_id, None)
        return
    assignment["slots"] = remaining
    if int(assignment.get("current_slot") or slot) == slot:
        assignment["current_slot"] = remaining[-1]
    assignment["last_used_at"] = time.time()


def _claim_new_target():
    with _locked_state() as state:
        targets = _refresh_pool(state)
        assignments = state["assignments"]
        slots = state["slots"]

        claimed_slots = {
            str(slot)
            for item in assignments.values()
            for slot in item.get("slots", [])
        }
        available = [
            slot
            for slot in sorted(slots, key=lambda value: int(value))
            if slot not in claimed_slots
        ]
        reclaimed_from = None

        if not available and len(slots) < _POOL_SIZE:
            slot, target_id = _create_target(state)
            if slot and target_id:
                targets[target_id] = {
                    "targetId": target_id,
                    "target_id": target_id,
                    "title": "",
                    "url": f"about:blank#codex-pool-{int(slot):02d}",
                }
                available = [slot]

        if not available:
            candidates = []
            for thread_id, assignment in assignments.items():
                active = _thread_is_active(thread_id)
                if not _fully_completed(assignment) and active:
                    continue
                completed_slots = assignment.get("completed_slots", {})
                candidate_slots = (
                    completed_slots.items()
                    if active
                    else (
                        (str(slot), completed_slots.get(
                            str(slot), assignment.get("last_used_at", 0)
                        ))
                        for slot in assignment.get("slots", [])
                    )
                )
                for raw_slot, completed_at in candidate_slots:
                    slot = int(raw_slot)
                    target_id = slots.get(str(slot))
                    if target_id in targets:
                        candidates.append((float(completed_at), thread_id, slot))
            candidates.sort()

            for _, thread_id, slot in candidates:
                if _thread_is_active(thread_id) and not _stop_thread_daemon(thread_id):
                    continue
                _archive_target(state, targets, thread_id, slot, "capacity")
                reclaimed_from = thread_id
                _remove_assignment_slot(assignments, thread_id, slot)
                available = [str(slot)]
                break

        # If no safely reclaimable completed/abandoned lease exists, grow past
        # the soft threshold instead of interrupting the user. The hard limit
        # remains a resource safety boundary.
        if not available and len(slots) < _HARD_LIMIT:
            slot, target_id = _create_target(state)
            if slot and target_id:
                targets[target_id] = {
                    "targetId": target_id,
                    "target_id": target_id,
                    "title": "",
                    "url": f"about:blank#codex-pool-{int(slot):02d}",
                }
                available = [slot]

        if not available:
            raise RuntimeError(
                f"Browser Use reached its safety limit ({_HARD_LIMIT}) and all "
                "tabs still have active leases. The task must wait for a lease "
                "to finish; active pages were preserved."
            )

        slot = available[0]
        target_id = slots[slot]
        assignment = assignments.setdefault(
            _THREAD_ID,
            {
                "slots": [],
                "current_slot": None,
                "completed_slots": {},
                "claimed_at": time.time(),
                "last_used_at": time.time(),
            },
        )
        assignment.setdefault("completed_slots", {}).pop(str(slot), None)
        assignment["slots"].append(int(slot))
        assignment["slots"] = sorted(set(assignment["slots"]))
        assignment["current_slot"] = int(slot)
        assignment["last_used_at"] = time.time()
        return {
            "target_id": target_id,
            "slot": int(slot),
            "reclaimed_from": reclaimed_from,
        }


def _evaluate_until_stable(expression, successes=3):
    stable = 0
    for _ in range(15):
        try:
            if _h.js(expression):
                stable += 1
                if stable >= successes:
                    return True
            else:
                stable = 0
        except Exception:
            stable = 0
        time.sleep(0.05)
    return False


def _running_title_expression(slot):
    fallback = f"工作中 {slot:02d}"
    return (
        "(()=>{let t=document.title||'';"
        "t=t.replace(/^🐴\\s*/u,'').replace(/^✓\\s*/u,'');"
        f"if(!t||t.startsWith('空闲 '))t={json.dumps(fallback)};"
        "document.title='🐴 '+t;return document.title.startsWith('🐴 ');})()"
    )


def _completed_title_expression(slot):
    fallback = f"任务 {slot:02d}"
    return (
        "(()=>{let t=document.title||'';"
        "t=t.replace(/^🐴\\s*/u,'').replace(/^✓\\s*/u,'');"
        f"if(!t||t.startsWith('工作中 '))t={json.dumps(fallback)};"
        "document.title='✓ '+t;return document.title.startsWith('✓ ');})()"
    )


def _attach_target(target_id, slot=None, title_state="running"):
    session_id = _h.cdp(
        "Target.attachToTarget",
        targetId=target_id,
        flatten=True,
    )["sessionId"]
    _h._send(
        {
            "meta": "set_session",
            "session_id": session_id,
            "target_id": target_id,
        }
    )
    if slot is not None and title_state == "running":
        _evaluate_until_stable(_running_title_expression(slot))
    elif slot is not None and title_state == "completed":
        _evaluate_until_stable(_completed_title_expression(slot))
    return session_id


def _set_blank_state(slot, state_name):
    marker = f"about:blank#codex-pool-{slot:02d}"
    title = f"空闲 {slot:02d}" if state_name == "idle" else f"🐴 工作中 {slot:02d}"
    _BASE_GOTO_URL(marker)
    expression = (
        "(()=>{"
        f"if(location.href!=={json.dumps(marker)})return false;"
        f"document.title={json.dumps(title)};"
        "if(document.body){document.body.replaceChildren();"
        "for(const a of [...document.body.attributes])"
        "document.body.removeAttribute(a.name);}"
        f"return document.title==={json.dumps(title)};"
        "})()"
    )
    if not _evaluate_until_stable(expression):
        raise RuntimeError(f"Could not reset Browser Use pool slot {slot:02d}")


def _owned_snapshot(state, targets):
    assignment = state["assignments"].get(_THREAD_ID)
    if not assignment:
        return None, {}
    owned = {
        state["slots"].get(str(slot)): int(slot)
        for slot in assignment["slots"]
        if state["slots"].get(str(slot)) in targets
    }
    return assignment, owned


def _target_owner(state, target_id):
    for thread_id, assignment in state["assignments"].items():
        for slot in assignment.get("slots", []):
            if state["slots"].get(str(slot)) == target_id:
                return thread_id, assignment, int(slot)
    return None, None, None


def _claim_existing_target(state, targets, target_id):
    owner, assignment, slot = _target_owner(state, target_id)
    if owner is None:
        slot = next(
            (int(raw) for raw, value in state["slots"].items() if value == target_id),
            None,
        )
    elif owner != _THREAD_ID:
        completed = str(slot) in assignment.get("completed_slots", {})
        if not completed and _thread_is_active(owner):
            raise RuntimeError(
                f"Tab is actively leased by Codex thread {owner}; wait for it to "
                "finish instead of taking over a live page"
            )
        _remove_assignment_slot(state["assignments"], owner, slot)
    if slot is None or target_id not in targets:
        raise RuntimeError("The requested isolated-browser tab no longer exists")
    current = state["assignments"].setdefault(
        _THREAD_ID,
        {"slots": [], "current_slot": slot, "completed_slots": {},
         "claimed_at": time.time(), "last_used_at": time.time()},
    )
    current["slots"] = sorted(set(current.get("slots", []) + [slot]))
    current["current_slot"] = slot
    current.setdefault("completed_slots", {}).pop(str(slot), None)
    current["last_used_at"] = time.time()
    return current, target_id, slot


def _resolve_requested_target(state, targets, target=None):
    assignment, owned = _owned_snapshot(state, targets)
    if not assignment and target is None:
        raise RuntimeError("This Codex thread does not own any Browser Use tabs")
    requested = (
        target.get("targetId") or target.get("target_id")
        if isinstance(target, dict)
        else target
    )
    if requested is None:
        requested = state["slots"].get(str(assignment["current_slot"]))
    if requested not in owned:
        assignment, requested, slot = _claim_existing_target(state, targets, requested)
        return assignment, {requested: slot}, requested, slot
    return assignment, owned, requested, owned[requested]


def _mark_slot_running(target=None):
    with _locked_state() as state:
        targets = _refresh_pool(state)
        assignment, _, requested, slot = _resolve_requested_target(
            state, targets, target
        )
        assignment.setdefault("completed_slots", {}).pop(str(slot), None)
        assignment["current_slot"] = slot
        assignment["last_used_at"] = time.time()
    return requested, slot


def switch_tab(target):
    """Switch to a tab, safely taking over only a completed or abandoned lease."""
    target_id, slot = _mark_slot_running(target)
    return _attach_target(target_id, slot, "running")


def resume_tab(target=None):
    """Resume a completed tab so it cannot be reclaimed while work continues."""
    target_id, slot = _mark_slot_running(target)
    _attach_target(target_id, slot, "running")
    return target_id


def goto_url(url):
    """Navigate the current owned tab and mark it as running."""
    target_id, slot = _mark_slot_running()
    _attach_target(target_id, slot, "running")
    return _BASE_GOTO_URL(url)


def new_tab(url="about:blank"):
    """Create tabs on demand; archive completed work only at the hard limit."""
    claim = _claim_new_target()
    target_id = claim["target_id"]
    slot = claim["slot"]
    _attach_target(target_id, slot, "running")
    if claim["reclaimed_from"] is not None:
        # Clear the previous completed page before exposing this target to its
        # new owner. This also handles same-document about:blank# navigation.
        _set_blank_state(slot, "running")
    if url != "about:blank":
        _BASE_GOTO_URL(url)
    elif claim["reclaimed_from"] is None:
        _set_blank_state(slot, "running")
    return target_id


def complete_tab(target=None):
    """Preserve one page for inspection and make it reclaimable when the pool fills."""
    with _locked_state() as state:
        targets = _refresh_pool(state)
        assignment, _, requested, slot = _resolve_requested_target(
            state, targets, target
        )
        completed_at = time.time()
        assignment.setdefault("completed_slots", {})[str(slot)] = completed_at
        assignment["current_slot"] = slot
        assignment["last_used_at"] = completed_at
    _attach_target(requested, slot, "completed")
    return True


def complete_thread_tabs():
    """Preserve every page owned by this thread and mark the thread complete."""
    with _locked_state() as state:
        targets = _refresh_pool(state)
        assignment, owned = _owned_snapshot(state, targets)
        if not assignment:
            return 0
        completed_at = time.time()
        assignment["completed_slots"] = {
            str(slot): completed_at for slot in assignment["slots"]
        }
        assignment["last_used_at"] = completed_at
        current_slot = int(assignment["current_slot"])
        items = [(target_id, slot) for target_id, slot in owned.items()]
        current_target = state["slots"].get(str(current_slot))
    for target_id, slot in items:
        _attach_target(target_id, slot, "completed")
    if current_target:
        _attach_target(current_target, current_slot, "completed")
    return len(items)


def show_tab(target=None):
    """Explicitly bring an owned tab and isolated Chrome to the foreground."""
    with _locked_state() as state:
        targets = _refresh_pool(state)
        assignment, _, requested, slot = _resolve_requested_target(
            state, targets, target
        )
        completed = str(slot) in assignment.get("completed_slots", {})
        if completed:
            assignment["completed_slots"][str(slot)] = time.time()
        assignment["current_slot"] = slot
        assignment["last_used_at"] = time.time()
    _attach_target(
        requested,
        slot,
        "completed" if completed else "running",
    )
    _h.cdp("Target.activateTarget", targetId=requested)
    return requested


def close_tab(target=None):
    """Explicitly erase and release one owned tab."""
    next_target = None
    next_slot = None
    next_state = "running"
    with _locked_state() as state:
        targets = _refresh_pool(state)
        assignment, _, requested, slot = _resolve_requested_target(
            state, targets, target
        )
        _attach_target(requested, slot, title_state=None)
        _set_blank_state(slot, "idle")
        _remove_assignment_slot(state["assignments"], _THREAD_ID, slot)
        remaining = state["assignments"].get(_THREAD_ID)
        if remaining:
            next_slot = int(remaining["current_slot"])
            next_target = state["slots"].get(str(next_slot))
            if str(next_slot) in remaining.get("completed_slots", {}):
                next_state = "completed"
    if next_target:
        _attach_target(next_target, next_slot, next_state)
    return True


def thread_tabs():
    """List all isolated tabs; ownership is a lease, not a visibility boundary."""
    with _locked_state() as state:
        targets = _refresh_pool(state)
        out = []
        ordered_slots = sorted(
            state["slots"].items(), key=lambda item: int(item[0])
        )
        for raw_slot, target_id in ordered_slots:
            if target_id not in targets:
                continue
            slot = int(raw_slot)
            owner, assignment, _ = _target_owner(state, target_id)
            completed = (assignment or {}).get("completed_slots", {})
            tab = dict(targets[target_id])
            tab["slot"] = slot
            tab["owner_thread"] = owner
            tab["current"] = bool(
                owner == _THREAD_ID
                and slot == int(assignment["current_slot"])
            )
            tab["status"] = (
                "idle"
                if owner is None
                else ("completed" if str(slot) in completed else "running")
            )
            tab["claimable"] = (
                owner is None
                or str(slot) in completed
                or not _thread_is_active(owner)
            )
            tab["completed_at"] = completed.get(str(slot))
            out.append(tab)
        return out


def list_tabs():
    """Compatibility alias for the shared isolated-tab inventory."""
    return thread_tabs()


def close_thread_tabs():
    """Explicitly erase and release every tab owned by this thread."""
    released = 0
    while True:
        tabs = [tab for tab in thread_tabs() if tab.get("owner_thread") == _THREAD_ID]
        if not tabs:
            return released
        close_tab(tabs[-1])
        released += 1


def label_idle_tabs():
    """Label unclaimed pool tabs as 空闲 01..12 for human inspection."""
    labeled = 0
    with _locked_state() as state:
        targets = _refresh_pool(state)
        claimed = {
            int(slot)
            for assignment in state["assignments"].values()
            for slot in assignment.get("slots", [])
        }
        for raw_slot, target_id in sorted(
            state["slots"].items(), key=lambda item: int(item[0])
        ):
            slot = int(raw_slot)
            if slot in claimed or target_id not in targets:
                continue
            _attach_target(target_id, slot, title_state=None)
            _set_blank_state(slot, "idle")
            labeled += 1
    return labeled


def tab_pool_status():
    """Return idle/running/completed/reclaimable pool diagnostics."""
    with _locked_state() as state:
        _refresh_pool(state)
        assignment = state["assignments"].get(_THREAD_ID)
        claimed = sum(
            len(item.get("slots", [])) for item in state["assignments"].values()
        )
        completed = sum(
            len(item.get("completed_slots", {}))
            for item in state["assignments"].values()
        )
        reclaimable = 0
        for thread_id, item in state["assignments"].items():
            if thread_id == _THREAD_ID or _fully_completed(item):
                reclaimable += len(item.get("completed_slots", {}))
        return {
            "soft_limit": _POOL_SIZE,
            "hard_limit": _HARD_LIMIT,
            "pool_size": len(state["slots"]),
            "claimed": claimed,
            "idle": len(state["slots"]) - claimed,
            "running": claimed - completed,
            "completed": completed,
            "reclaimable": reclaimable,
            "effective_available": len(state["slots"]) - claimed + reclaimable,
            "can_grow": len(state["slots"]) < _HARD_LIMIT,
            "thread_id": _THREAD_ID,
            "thread_slots": assignment.get("slots", []) if assignment else [],
            "thread_completed_slots": sorted(
                int(slot)
                for slot in (assignment or {}).get("completed_slots", {})
            ),
            "current_slot": assignment.get("current_slot") if assignment else None,
        }
