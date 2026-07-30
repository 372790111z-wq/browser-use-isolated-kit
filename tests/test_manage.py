from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MANAGE = ROOT / "scripts/manage.py"
SPEC = importlib.util.spec_from_file_location("manage", MANAGE)
manage = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(manage)


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.site = self.root / "site-packages"
        self.chrome = self.root / "Google Chrome"
        self.browser_use = self.home / ".local/bin/browser-use"
        self.skill = self.home / ".codex/skills/browser-use/SKILL.md"
        self.port = 59433

        self.browser_use.parent.mkdir(parents=True)
        self.browser_use.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.browser_use.chmod(0o755)
        self.chrome.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.chrome.chmod(0o755)
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text(
            "---\nname: browser-use\ndescription: test\n---\n\n# Browser Use\n\nBase.\n",
            encoding="utf-8",
        )
        for relative, replacements in manage.PATCHES.items():
            path = self.site / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "\n\n".join(old for old, _ in replacements) + "\n",
                encoding="utf-8",
            )

    def tearDown(self):
        self.temp.cleanup()

    def command(self, action, *extra):
        return [
            sys.executable,
            str(MANAGE),
            action,
            "--home",
            str(self.home),
            "--browser-use-bin",
            str(self.browser_use),
            "--chrome-bin",
            str(self.chrome),
            "--site-packages",
            str(self.site),
            "--skill-file",
            str(self.skill),
            "--allow-non-macos",
            "--port",
            str(self.port),
            *extra,
        ]

    def test_install_is_idempotent_and_uninstall_restores(self):
        first = subprocess.run(self.command("install"), text=True, capture_output=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        wrapper = self.home / ".local/bin/browser-use-isolated"
        helper = (
            self.home
            / ".config/browser-harness/agent-workspace-isolated/agent_helpers.py"
        )
        self.assertTrue(wrapper.exists())
        self.assertTrue(helper.exists())
        self.assertIn(
            manage.POLICY_START,
            self.skill.read_text(encoding="utf-8"),
        )
        self.assertNotIn("@@", wrapper.read_text(encoding="utf-8"))

        state_path = self.home / ".config/browser-use-isolated-kit/install-state.json"
        first_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(first_state["patched_files"]), len(manage.PATCHES))

        second = subprocess.run(self.command("install"), text=True, capture_output=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        second_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(first_state["patched_files"], second_state["patched_files"])
        self.assertEqual(
            self.skill.read_text(encoding="utf-8").count(manage.POLICY_START),
            1,
        )

        removed = subprocess.run(
            self.command("uninstall", "--yes"),
            text=True,
            capture_output=True,
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(wrapper.exists())
        self.assertNotIn(
            manage.POLICY_START,
            self.skill.read_text(encoding="utf-8"),
        )
        for relative, replacements in manage.PATCHES.items():
            content = (self.site / relative).read_text(encoding="utf-8")
            for old, _ in replacements:
                self.assertIn(old, content)

    def test_doctor_reports_missing_install(self):
        result = subprocess.run(
            self.command("doctor"),
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL wrapper", result.stdout)

    def test_upstream_upgrade_uses_existing_uv_tool_environment(self):
        uv_browser_use = (
            self.home
            / ".local/share/uv/tools/browser-use/bin/browser-use"
        )
        uv_browser_use.parent.mkdir(parents=True)
        uv_browser_use.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.browser_use.unlink()
        self.browser_use.symlink_to(uv_browser_use)
        args = manage.parser().parse_args(
            self.command("update", "--upstream")[2:]
        )
        layout = manage.Layout(args)

        with mock.patch.object(
            manage.shutil,
            "which",
            return_value="/opt/homebrew/bin/uv",
        ):
            self.assertEqual(
                manage.upstream_upgrade_command(layout),
                [
                    "/opt/homebrew/bin/uv",
                    "tool",
                    "upgrade",
                    "browser-use",
                ],
            )

    def test_upstream_upgrade_keeps_official_fallback_for_other_installs(self):
        args = manage.parser().parse_args(
            self.command("update", "--upstream")[2:]
        )
        layout = manage.Layout(args)
        self.assertEqual(
            manage.upstream_upgrade_command(layout),
            [str(self.browser_use), "--update", "-y"],
        )


if __name__ == "__main__":
    unittest.main()
