#!/usr/bin/env python3
"""Keep Chrome DevTools MCP page selection in the background by default."""

from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


_pending_focus = {}
_pending_focus_lock = threading.Lock()


def frontmost_pid():
    script = (
        'ObjC.import("AppKit"); function run() {'
        'return String(ObjC.unwrap('
        '$.NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier));}'
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
        text=True,
        capture_output=True,
    )
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdigit() else None


def isolated_process(pid: int) -> bool:
    profile = os.environ.get("BROWSER_USE_ISOLATED_PROFILE_DIR")
    if not profile:
        return False
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        text=True,
        capture_output=True,
    )
    return result.returncode == 0 and f"--user-data-dir={profile}" in result.stdout


def restore_focus_if_isolated(previous_pid) -> None:
    if not isinstance(previous_pid, int):
        return
    current_pid = frontmost_pid()
    if current_pid == previous_pid or not current_pid or not isolated_process(current_pid):
        return
    script = (
        'ObjC.import("AppKit"); function run(argv) {'
        'const app=$.NSRunningApplication.runningApplicationWithProcessIdentifier('
        'parseInt(argv[0],10)); if(app) app.activateWithOptions(3);}'
    )
    subprocess.run(
        [
            "/usr/bin/osascript", "-l", "JavaScript", "-e", script,
            str(previous_pid),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def request_id(line: bytes):
    try:
        message = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return message.get("id")


def remember_focus(line: bytes) -> None:
    identifier = request_id(line)
    if identifier is None:
        return
    with _pending_focus_lock:
        _pending_focus[json.dumps(identifier)] = frontmost_pid()


def restore_focus_for_response(line: bytes) -> None:
    identifier = request_id(line)
    if identifier is None:
        return
    with _pending_focus_lock:
        previous_pid = _pending_focus.pop(json.dumps(identifier), None)
    if previous_pid is None:
        return
    # Chrome can activate just after the CDP operation resolves. A short host
    # delay makes restoration occur after that asynchronous AppKit event.
    time.sleep(0.25)
    restore_focus_if_isolated(previous_pid)


def request_needs_browser(line: bytes) -> bool:
    try:
        message = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return message.get("method") == "tools/call"


def cdp_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/json/version", timeout=1):
            return True
    except (OSError, urllib.error.URLError):
        return False


def ensure_browser() -> None:
    wrapper = os.environ.get("BROWSER_USE_ISOLATED_WRAPPER")
    cdp_url = os.environ.get("BROWSER_USE_ISOLATED_CDP_URL")
    log_file = os.environ.get("BROWSER_USE_ISOLATED_LOG_FILE")
    if not wrapper or not cdp_url or not log_file:
        raise RuntimeError("isolated browser lazy-start environment is incomplete")
    if cdp_ready(cdp_url):
        return
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = log_path.with_suffix(log_path.suffix + ".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if cdp_ready(cdp_url):
            return
        env = os.environ.copy()
        env["CODEX_THREAD_ID"] = "mcp-bootstrap"
        with log_path.open("ab") as log:
            result = subprocess.run(
                [wrapper],
                input=b"print(tab_pool_status())\n",
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
        if result.returncode or not cdp_ready(cdp_url):
            raise RuntimeError(
                f"dedicated Chrome failed to start; inspect {log_path}"
            )


def protect_request(line: bytes) -> bytes:
    """Rewrite focus-capable MCP tool calls without changing other traffic."""
    try:
        message = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return line
    if message.get("method") != "tools/call":
        return line
    params = message.get("params")
    if not isinstance(params, dict):
        return line
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
        params["arguments"] = arguments
    name = params.get("name")
    if name == "new_page":
        # Upstream defaults to foreground. An autonomous isolated task cannot
        # override this safety default.
        arguments["background"] = True
        arguments.pop("isolatedContext", None)
    elif name == "select_page":
        # Explicit presentation uses Browser Use's gated show_tab() helper.
        arguments["bringToFront"] = False
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def relay(
    source, destination, *, protect: bool = False, restore_responses: bool = False
) -> None:
    for line in iter(source.readline, b""):
        if protect and request_needs_browser(line):
            remember_focus(line)
            ensure_browser()
        if restore_responses:
            restore_focus_for_response(line)
        destination.write(protect_request(line) if protect else line)
        destination.flush()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mcp-stdio-guard.py COMMAND [ARG ...]", file=sys.stderr)
        return 2
    child = subprocess.Popen(
        sys.argv[1:],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stdin is not None
    assert child.stdout is not None
    assert child.stderr is not None
    threading.Thread(
        target=relay,
        args=(child.stdout, sys.stdout.buffer),
        kwargs={"restore_responses": True},
        daemon=True,
    ).start()
    threading.Thread(
        target=relay, args=(child.stderr, sys.stderr.buffer), daemon=True
    ).start()
    try:
        relay(sys.stdin.buffer, child.stdin, protect=True)
    finally:
        child.stdin.close()
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
