#!/usr/bin/env python3
"""Install and maintain the macOS Browser Use isolated environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_START = "<!-- browser-use-isolated-kit:policy:start -->"
POLICY_END = "<!-- browser-use-isolated-kit:policy:end -->"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic(path: Path, data: str, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(data, encoding="utf-8")
    if mode is not None:
        temp.chmod(mode)
    temp.replace(path)


def run(
    command: list[str],
    *,
    check: bool = True,
    env: Optional[dict[str, str]] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=check,
        env=env,
        input=input_text,
    )


class Layout:
    def __init__(self, args: argparse.Namespace):
        self.home = Path(args.home).expanduser().resolve()
        self.browser_use_bin = Path(
            args.browser_use_bin or self.home / ".local/bin/browser-use"
        ).expanduser()
        self.chrome_bin = Path(
            args.chrome_bin
            or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        ).expanduser()
        self.chrome_app = args.chrome_app
        self.profile_dir = self.home / ".browser-use/profiles/codex-isolated"
        self.workspace = (
            self.home / ".config/browser-harness/agent-workspace-isolated"
        )
        self.config_dir = self.home / ".config/browser-use-isolated-kit"
        self.config_path = self.config_dir / "config.json"
        self.state_path = self.config_dir / "install-state.json"
        self.backup_dir = self.config_dir / "backups"
        self.wrapper = self.home / ".local/bin/browser-use-isolated"
        self.skill_file = Path(
            args.skill_file
            or self.home / ".codex/skills/browser-use/SKILL.md"
        ).expanduser()
        self.setup_skill_dir = self.home / ".codex/skills/browser-use-isolated"
        saved_config = {}
        if self.config_path.exists():
            try:
                saved_config = json.loads(
                    self.config_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                saved_config = {}
        self.port = (
            args.port
            if args.port is not None
            else int(saved_config.get("cdp_port") or 9333)
        )
        self.pool_size = args.pool_size
        self.site_packages = (
            Path(args.site_packages).expanduser()
            if args.site_packages
            else self._discover_site_packages()
        )

    def _discover_site_packages(self) -> Optional[Path]:
        resolved = self.browser_use_bin.resolve(strict=False)
        candidates = []
        if resolved.exists():
            tool_root = resolved.parent.parent
            candidates.extend(tool_root.glob("lib/python*/site-packages"))
        candidates.extend(
            (self.home / ".local/share/uv/tools/browser-use").glob(
                "lib/python*/site-packages"
            )
        )
        for candidate in candidates:
            if (candidate / "browser_harness").is_dir():
                return candidate
        return None


def policy_block(layout: Layout) -> str:
    return f"""{POLICY_START}
## Device-local isolated browser policy

For local browser work on this Mac, always invoke `browser-use-isolated`.

- Wrapper: `{layout.wrapper}`
- Dedicated profile: `{layout.profile_dir}`
- Dedicated CDP: `http://127.0.0.1:{layout.port}`
- Per-thread daemon: `codex-isolated-$CODEX_THREAD_ID`
- Never auto-discover or fall back to the user's everyday Chrome.
- The isolation boundary is the installed Google Chrome application plus a
  separate profile, process, CDP endpoint, and observable tab pool. A second
  Chrome application is not required.

On failure, run `browser-use-isolated --doctor`, retain the original error,
and distinguish application-path, CDP, permission, tab-pool, and daemon
problems. If isolation cannot be restored safely, stop and report the blocker.
Shared Chrome requires an explicit user request.

Use `new_tab(url)` for the first page. At normal completion call
`complete_thread_tabs()` so results remain visible and reclaimable. Use
`show_tab()` only when the user explicitly asks to see the controlled page.
{POLICY_END}"""


def patch_skill(layout: Layout) -> None:
    if not layout.skill_file.exists():
        raise RuntimeError(
            f"Browser Use Skill not found: {layout.skill_file}. "
            "Run `browser-use skill install` first."
        )
    content = layout.skill_file.read_text(encoding="utf-8")
    block = policy_block(layout)
    if POLICY_START in content and POLICY_END in content:
        before, tail = content.split(POLICY_START, 1)
        _, after = tail.split(POLICY_END, 1)
        updated = before.rstrip() + "\n\n" + block + after
    else:
        legacy_heading = "## Device-local isolated browser policy"
        if legacy_heading in content:
            before, legacy_tail = content.split(legacy_heading, 1)
            if "browser-use-isolated" not in legacy_tail:
                raise RuntimeError(
                    "Found an unrelated legacy policy section; refusing migration"
                )
            if "\n## " in legacy_tail:
                _, after = legacy_tail.split("\n## ", 1)
                content = before.rstrip() + "\n\n## " + after
            else:
                content = before.rstrip() + "\n"
        anchor = "# Browser Use"
        if anchor not in content:
            raise RuntimeError("Browser Use Skill has no '# Browser Use' heading")
        updated = content.replace(anchor, anchor + "\n\n" + block, 1)
    write_atomic(layout.skill_file, updated)


def package_patch_status(layout: Layout) -> tuple[bool, str]:
    if layout.site_packages is None:
        return False, "site-packages not found"
    missing = []
    for relative, replacements in PATCHES.items():
        path = layout.site_packages / relative
        if not path.exists():
            missing.append(relative)
            continue
        content = path.read_text(encoding="utf-8")
        for _, replacement in replacements:
            if replacement not in content:
                missing.append(relative)
                break
    if missing:
        return False, ", ".join(sorted(set(missing)))
    return True, str(layout.site_packages)


def port_ownership_status(layout: Layout) -> tuple[bool, str]:
    result = run(
        [
            "lsof",
            "-t",
            "-nP",
            f"-iTCP:{layout.port}",
            "-sTCP:LISTEN",
        ],
        check=False,
    )
    pids = sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})
    if not pids:
        return True, f"{layout.port} is available"
    commands = []
    for pid in pids:
        try:
            process = run(
                ["/bin/ps", "-p", pid, "-o", "command="],
                check=False,
            )
        except OSError as exc:
            return False, f"cannot verify owner of port {layout.port}: {exc}"
        command = process.stdout.strip()
        commands.append(command or f"pid {pid}")
    expected_profile = f"--user-data-dir={layout.profile_dir}"
    expected_port = f"--remote-debugging-port={layout.port}"
    if all(
        expected_profile in command and expected_port in command
        for command in commands
    ):
        return True, f"{layout.port} is owned by the isolated Chrome"
    labels = [command.split()[0] if command else "unknown" for command in commands]
    return False, f"{layout.port} is occupied by: {', '.join(labels)}"


PATCHES = {
    "browser_use/browser/session.py": [
        (
            "import asyncio\nimport logging\nimport re\n",
            "import asyncio\nimport logging\nimport os\nimport re\n",
        ),
        (
            "\t\t# Visually switch to the tab in the browser\n"
            "\t\t# The Force Background Tab extension prevents Chrome from auto-switching when links create new tabs,\n"
            "\t\t# but we still want the agent to be able to explicitly switch tabs when needed\n"
            "\t\tawait cdp_session.cdp_client.send.Target.activateTarget(params={'targetId': event.target_id})",
            "\t\t# Keep dedicated automation Chrome in the background when requested by\n"
            "\t\t# the local Codex wrapper. CDP commands remain scoped to the selected\n"
            "\t\t# session, so visual tab activation is not required for automation.\n"
            "\t\tif os.environ.get('BROWSER_USE_BACKGROUND_TABS') != '1':\n"
            "\t\t\tawait cdp_session.cdp_client.send.Target.activateTarget(params={'targetId': event.target_id})",
        ),
    ],
    "browser_use/browser/session_manager.py": [
        (
            "from collections import deque\nfrom typing import TYPE_CHECKING, Any\n",
            "from collections import deque\nimport os\nfrom typing import TYPE_CHECKING, Any\n",
        ),
        (
            "\t\t\t\tif is_existing_tab:\n"
            "\t\t\t\t\ttry:\n"
            "\t\t\t\t\t\tassert self.browser_session._cdp_client_root is not None\n"
            "\t\t\t\t\t\tawait self.browser_session._cdp_client_root.send.Target.activateTarget",
            "\t\t\t\tif is_existing_tab and os.environ.get('BROWSER_USE_BACKGROUND_TABS') != '1':\n"
            "\t\t\t\t\ttry:\n"
            "\t\t\t\t\t\tassert self.browser_session._cdp_client_root is not None\n"
            "\t\t\t\t\t\tawait self.browser_session._cdp_client_root.send.Target.activateTarget",
        ),
    ],
    "browser_harness/helpers.py": [
        (
            "    cdp(\"Target.activateTarget\", targetId=target_id)\n"
            "    sid = cdp(\"Target.attachToTarget\", targetId=target_id, flatten=True)",
            "    if os.environ.get(\"BROWSER_USE_BACKGROUND_TABS\") != \"1\":\n"
            "        cdp(\"Target.activateTarget\", targetId=target_id)\n"
            "    sid = cdp(\"Target.attachToTarget\", targetId=target_id, flatten=True)",
        ),
    ],
    "browser_harness/daemon.py": [
        (
            "        pages = [t for t in targets if is_real_page(t)]\n"
            "        if not pages:\n",
            "        pages = [t for t in targets if is_real_page(t)]\n"
            "        if not pages and os.environ.get(\"BROWSER_USE_BACKGROUND_TABS\") == \"1\":\n"
            "            # The Codex isolated browser intentionally starts with a pool of\n"
            "            # about:blank tabs. Reuse one instead of creating a 13th tab,\n"
            "            # which makes Chrome activate itself on macOS.\n"
            "            pages = [t for t in targets if t.get(\"type\") == \"page\"]\n"
            "        if not pages:\n",
        ),
    ],
}


def patch_packages(layout: Layout, state: dict) -> None:
    if layout.site_packages is None:
        raise RuntimeError("Could not locate Browser Use site-packages")
    patched_files = {}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = layout.backup_dir / stamp
    for relative, replacements in PATCHES.items():
        path = layout.site_packages / relative
        if not path.exists():
            raise RuntimeError(f"Required Browser Use file not found: {path}")
        original = path.read_text(encoding="utf-8")
        updated = original
        changed = False
        for old, new in replacements:
            if new in updated:
                continue
            if old not in updated:
                raise RuntimeError(
                    f"Unsupported upstream version: patch anchor missing in {relative}"
                )
            updated = updated.replace(old, new, 1)
            changed = True
        if changed:
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            write_atomic(path, updated)
            patched_files[relative] = {
                "path": str(path),
                "backup": str(backup),
                "patched_sha256": sha256(path),
            }
    if patched_files:
        state["patched_files"] = patched_files


def render_wrapper(layout: Layout) -> str:
    template = (REPO_ROOT / "templates/browser-use-isolated.zsh").read_text(
        encoding="utf-8"
    )
    values = {
        "@@BROWSER_USE_BIN@@": str(layout.browser_use_bin),
        "@@CHROME_APP@@": layout.chrome_app,
        "@@CHROME_BIN@@": str(layout.chrome_bin),
        "@@PROFILE_DIR@@": str(layout.profile_dir),
        "@@AGENT_WORKSPACE@@": str(layout.workspace),
        "@@POOL_SIZE@@": str(layout.pool_size),
        "@@CDP_PORT@@": str(layout.port),
    }
    for token, value in values.items():
        template = template.replace(token, value)
    if "@@" in template:
        raise RuntimeError("Unresolved wrapper template token")
    return template


def install(args: argparse.Namespace) -> int:
    layout = Layout(args)
    if sys.version_info < (3, 9):
        raise RuntimeError("Python 3.9 or newer is required")
    if not (1 <= layout.port <= 65535):
        raise RuntimeError("CDP port must be between 1 and 65535")
    if not (1 <= layout.pool_size <= 100):
        raise RuntimeError("Pool size must be between 1 and 100")
    port_ok, port_detail = port_ownership_status(layout)
    if not port_ok:
        raise RuntimeError(
            f"{port_detail}. Choose a different port; do not attach to it."
        )
    if platform.system() != "Darwin" and not args.allow_non_macos:
        raise RuntimeError("This kit currently supports macOS only")
    missing = []
    for path, label in (
        (layout.browser_use_bin, "browser-use"),
        (layout.chrome_bin, "Google Chrome"),
    ):
        if not path.exists():
            missing.append(f"{label}: {path}")
    for command in ("curl", "jq", "lsof", "zsh"):
        if shutil.which(command) is None:
            missing.append(command)
    if missing:
        raise RuntimeError("Missing prerequisites:\n- " + "\n- ".join(missing))

    previous_state = {}
    if layout.state_path.exists():
        try:
            previous_state = json.loads(
                layout.state_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            previous_state = {}
    state = {
        "version": 1,
        "installed_at": time.time(),
        "repo": str(REPO_ROOT),
        "wrapper": str(layout.wrapper),
        "profile_dir": str(layout.profile_dir),
        "workspace": str(layout.workspace),
        "skill_file": str(layout.skill_file),
        "setup_skill_dir": str(layout.setup_skill_dir),
        "port": layout.port,
        "pool_size": layout.pool_size,
        "patched_files": previous_state.get("patched_files", {}),
    }
    layout.profile_dir.mkdir(parents=True, exist_ok=True)
    (layout.workspace / "tab-pool").mkdir(parents=True, exist_ok=True)
    write_atomic(layout.wrapper, render_wrapper(layout), 0o755)
    shutil.copy2(
        REPO_ROOT / "templates/agent_helpers.py",
        layout.workspace / "agent_helpers.py",
    )
    managed_marker = layout.setup_skill_dir / ".browser-use-isolated-kit-managed"
    if layout.setup_skill_dir.exists() and not managed_marker.exists():
        raise RuntimeError(
            f"Refusing to overwrite unmanaged Skill: {layout.setup_skill_dir}"
        )
    shutil.copytree(
        REPO_ROOT / "skill/browser-use-isolated",
        layout.setup_skill_dir,
        dirs_exist_ok=True,
    )
    write_atomic(managed_marker, f"{REPO_ROOT}\n")
    patch_packages(layout, state)
    patch_skill(layout)
    config = {
        "browser_use_bin": str(layout.browser_use_bin),
        "chrome_app": layout.chrome_app,
        "chrome_bin": str(layout.chrome_bin),
        "profile_dir": str(layout.profile_dir),
        "agent_workspace": str(layout.workspace),
        "cdp_port": layout.port,
        "pool_size": layout.pool_size,
    }
    write_atomic(layout.config_path, json.dumps(config, indent=2) + "\n")
    write_atomic(layout.state_path, json.dumps(state, indent=2) + "\n")
    print(f"Installed Browser Use isolated kit: {layout.wrapper}")
    print("Run `python3 scripts/manage.py doctor` before live browser work.")
    return 0


def doctor(args: argparse.Namespace) -> int:
    layout = Layout(args)
    patches_ok, patches_detail = package_patch_status(layout)
    port_ok, port_detail = port_ownership_status(layout)
    checks = [
        ("platform", platform.system() == "Darwin", platform.system()),
        ("browser-use", layout.browser_use_bin.exists(), str(layout.browser_use_bin)),
        ("chrome", layout.chrome_bin.exists(), str(layout.chrome_bin)),
        ("wrapper", layout.wrapper.exists(), str(layout.wrapper)),
        (
            "agent_helpers",
            (layout.workspace / "agent_helpers.py").exists(),
            str(layout.workspace / "agent_helpers.py"),
        ),
        ("skill", layout.skill_file.exists(), str(layout.skill_file)),
        (
            "skill_policy",
            layout.skill_file.exists()
            and POLICY_START in layout.skill_file.read_text(encoding="utf-8"),
            str(layout.skill_file),
        ),
        (
            "setup_skill",
            (
                layout.setup_skill_dir
                / ".browser-use-isolated-kit-managed"
            ).exists(),
            str(layout.setup_skill_dir),
        ),
        ("site_packages", layout.site_packages is not None, str(layout.site_packages)),
        ("background_patches", patches_ok, patches_detail),
        ("cdp_port_ownership", port_ok, port_detail),
    ]
    failed = False
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        failed = failed or not ok
    if args.live and not failed:
        env = os.environ.copy()
        env["CODEX_THREAD_ID"] = "kit-doctor"
        warmup = run(
            [str(layout.wrapper)],
            check=False,
            env=env,
            input_text="print(tab_pool_status())\n",
        )
        print(warmup.stdout, end="")
        print(warmup.stderr, end="", file=sys.stderr)
        if warmup.returncode:
            return warmup.returncode
        result = run(
            [str(layout.wrapper), "--doctor"],
            check=False,
            env=env,
        )
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        failed = (
            result.returncode != 0
            or "[ok  ] chrome running" not in result.stdout
            or "codex-isolated-kit-doctor" not in result.stdout
        )
    return 1 if failed else 0


def upstream_upgrade_command(layout: Layout) -> list[str]:
    resolved = layout.browser_use_bin.resolve(strict=False)
    uv_tool_root = (
        layout.home / ".local/share/uv/tools/browser-use"
    ).resolve(strict=False)
    try:
        resolved.relative_to(uv_tool_root)
    except ValueError:
        return [str(layout.browser_use_bin), "--update", "-y"]

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError(
            "browser-use is managed by uv, but the uv command is unavailable"
        )
    return [uv, "tool", "upgrade", "browser-use"]


def upgrade_upstream(layout: Layout) -> int:
    command = upstream_upgrade_command(layout)
    result = run(command, check=False)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        return result.returncode
    skill_result = run(
        [str(layout.browser_use_bin), "skill", "install"],
        check=False,
    )
    print(skill_result.stdout, end="")
    print(skill_result.stderr, end="", file=sys.stderr)
    return skill_result.returncode


def update(args: argparse.Namespace) -> int:
    layout = Layout(args)
    if args.upstream:
        result = upgrade_upstream(layout)
        if result:
            return result
    return install(args)


def remove_policy(layout: Layout) -> None:
    if not layout.skill_file.exists():
        return
    content = layout.skill_file.read_text(encoding="utf-8")
    if POLICY_START not in content or POLICY_END not in content:
        return
    before, tail = content.split(POLICY_START, 1)
    _, after = tail.split(POLICY_END, 1)
    write_atomic(layout.skill_file, before.rstrip() + "\n\n" + after.lstrip())


def uninstall(args: argparse.Namespace) -> int:
    if not args.yes:
        raise RuntimeError("Uninstall requires --yes")
    layout = Layout(args)
    state = {}
    if layout.state_path.exists():
        state = json.loads(layout.state_path.read_text(encoding="utf-8"))
    remove_policy(layout)
    for item in state.get("patched_files", {}).values():
        path = Path(item["path"])
        backup = Path(item["backup"])
        if (
            path.exists()
            and backup.exists()
            and sha256(path) == item.get("patched_sha256")
        ):
            shutil.copy2(backup, path)
        elif path.exists():
            print(f"Preserved changed package file: {path}", file=sys.stderr)
    for path in (
        layout.wrapper,
        layout.workspace / "agent_helpers.py",
        layout.config_path,
        layout.state_path,
    ):
        if path.exists():
            path.unlink()
    managed_marker = layout.setup_skill_dir / ".browser-use-isolated-kit-managed"
    if managed_marker.exists():
        shutil.rmtree(layout.setup_skill_dir)
    if args.remove_profile and layout.profile_dir.exists():
        raise RuntimeError(
            "Profile deletion is intentionally not automated. Remove it manually "
            "only after confirming no login state is needed."
        )
    print(f"Uninstalled kit; profile preserved at {layout.profile_dir}")
    return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", default=str(Path.home()))
    common.add_argument("--browser-use-bin")
    common.add_argument("--chrome-bin")
    common.add_argument("--chrome-app", default="Google Chrome")
    common.add_argument("--skill-file")
    common.add_argument("--site-packages")
    common.add_argument("--port", type=int)
    common.add_argument("--pool-size", type=int, default=12)
    common.add_argument("--allow-non-macos", action="store_true")

    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("install", parents=[common])
    doctor_parser = sub.add_parser("doctor", parents=[common])
    doctor_parser.add_argument("--live", action="store_true")
    update_parser = sub.add_parser("update", parents=[common])
    update_parser.add_argument("--upstream", action="store_true")
    uninstall_parser = sub.add_parser("uninstall", parents=[common])
    uninstall_parser.add_argument("--yes", action="store_true")
    uninstall_parser.add_argument("--remove-profile", action="store_true")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return {
            "install": install,
            "doctor": doctor,
            "update": update,
            "uninstall": uninstall,
        }[args.command](args)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
