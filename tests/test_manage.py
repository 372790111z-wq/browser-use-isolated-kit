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
        self.chrome = (
            self.root / "Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        self.browser_use = self.home / ".local/bin/browser-use"
        self.skill = self.home / ".codex/skills/browser-use/SKILL.md"
        self.port = 59433

        self.browser_use.parent.mkdir(parents=True)
        self.browser_use.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.browser_use.chmod(0o755)
        self.chrome.parent.mkdir(parents=True)
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

    def test_first_install_selects_and_then_persists_port(self):
        args = manage.parser().parse_args(self.command("install")[2:])
        args.port = None
        with mock.patch.object(manage, "first_available_port", return_value=9341):
            first = manage.Layout(args)
        self.assertEqual(first.port, 9341)
        first.config_dir.mkdir(parents=True)
        first.config_path.write_text('{"cdp_port": 9341}\n', encoding="utf-8")
        with mock.patch.object(manage, "first_available_port", return_value=9350):
            second = manage.Layout(args)
        self.assertEqual(second.port, 9341)

    def test_generated_runtime_uses_dynamic_shared_leases(self):
        result = subprocess.run(self.command("install"), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        wrapper = (self.home / ".local/bin/browser-use-isolated").read_text(encoding="utf-8")
        helper = (
            self.home
            / ".config/browser-harness/agent-workspace-isolated/agent_helpers.py"
        ).read_text(encoding="utf-8")
        self.assertIn("readonly INITIAL_TABS=1", wrapper)
        self.assertIn(f'readonly CHROME_APP="{self.chrome.parent.parent.parent}"', wrapper)
        self.assertIn("BU_TAB_POOL_HARD_LIMIT", wrapper)
        self.assertIn('def list_tabs():', helper)
        self.assertIn('Target.createTarget', helper)
        self.assertIn('actively leased by Codex thread', helper)
        syntax = subprocess.run(
            ["/bin/zsh", "-n", str(self.home / ".local/bin/browser-use-isolated")],
            text=True,
            capture_output=True,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_mcp_wrapper_is_pinned_and_targets_persisted_cdp(self):
        installed = subprocess.run(
            self.command("install"), text=True, capture_output=True
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        args = manage.parser().parse_args(
            self.command(
                "configure-mcp",
                "--npm-bin",
                "/fake/npm",
                "--codex-bin",
                "/fake/codex",
            )[2:]
        )
        layout = manage.Layout(args)
        configured = False

        def fake_run(command, **kwargs):
            nonlocal configured
            if command[:2] == ["/fake/npm", "install"]:
                layout.mcp_bin.parent.mkdir(parents=True)
                layout.mcp_bin.write_text(
                    "#!/bin/sh\n"
                    "if [ \"$1\" = \"--version\" ]; then echo 1.6.0; "
                    "else echo browserUrl usageStatistics; fi\n",
                    encoding="utf-8",
                )
                layout.mcp_bin.chmod(0o755)
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:4] == ["/fake/codex", "mcp", "get", manage.MCP_NAME]:
                if configured:
                    return subprocess.CompletedProcess(
                        command, 0, f"command: {layout.mcp_wrapper}\n", ""
                    )
                return subprocess.CompletedProcess(command, 1, "", "missing")
            if command[:4] == ["/fake/codex", "mcp", "add", manage.MCP_NAME]:
                configured = True
                return subprocess.CompletedProcess(command, 0, "", "")
            if command == [str(layout.mcp_bin), "--version"]:
                return subprocess.CompletedProcess(command, 0, "1.6.0\n", "")
            if command == [str(layout.mcp_bin), "--help"]:
                return subprocess.CompletedProcess(
                    command, 0, "browserUrl usageStatistics\n", ""
                )
            self.fail(f"unexpected command: {command}")

        with mock.patch.object(manage, "run", side_effect=fake_run):
            self.assertEqual(manage.configure_mcp(args), 0)

        wrapper = layout.mcp_wrapper.read_text(encoding="utf-8")
        self.assertIn(f'readonly CDP_URL="http://127.0.0.1:{self.port}"', wrapper)
        self.assertIn('"--browser-url=$CDP_URL"', wrapper)
        self.assertIn(str(layout.mcp_bin), wrapper)
        self.assertNotIn("@latest", wrapper)
        self.assertEqual(manage.MCP_VERSION, "1.6.0")
        syntax = subprocess.run(
            ["/bin/zsh", "-n", str(layout.mcp_wrapper)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_mcp_update_stages_persists_and_rolls_back(self):
        update_args = manage.parser().parse_args(
            self.command(
                "update-mcp", "--npm-bin", "/fake/npm", "--to", "1.7.0"
            )[2:]
        )
        layout = manage.Layout(update_args)

        def write_fake_binary(root, version):
            binary = root / "node_modules/.bin/chrome-devtools-mcp"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text(
                "#!/bin/sh\n"
                f"if [ \"$1\" = \"--version\" ]; then echo {version}; "
                "else echo browserUrl usageStatistics; fi\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)

        write_fake_binary(layout.mcp_root, "1.6.0")
        layout.config_dir.mkdir(parents=True)
        layout.config_path.write_text(
            '{"cdp_port": 59433, "mcp_version": "1.6.0"}\n',
            encoding="utf-8",
        )

        def fake_run(command, **kwargs):
            if command[:2] == ["/fake/npm", "install"]:
                prefix = Path(command[command.index("--prefix") + 1])
                version = command[-1].rsplit("@", 1)[-1]
                write_fake_binary(prefix, version)
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.run(command, text=True, capture_output=True)

        with mock.patch.object(manage, "run", side_effect=fake_run):
            self.assertEqual(manage.update_mcp(update_args), 0)
        updated_config = json.loads(layout.config_path.read_text(encoding="utf-8"))
        self.assertEqual(updated_config["mcp_version"], "1.7.0")
        self.assertEqual(manage.mcp_binary_version(layout.mcp_bin), "1.7.0")
        self.assertTrue(layout.mcp_root.with_name("mcp.previous").exists())

        rollback_args = manage.parser().parse_args(
            self.command("rollback-mcp", "--yes")[2:]
        )
        self.assertEqual(manage.rollback_mcp(rollback_args), 0)
        rolled_back_config = json.loads(
            layout.config_path.read_text(encoding="utf-8")
        )
        self.assertEqual(rolled_back_config["mcp_version"], "1.6.0")
        self.assertEqual(manage.mcp_binary_version(layout.mcp_bin), "1.6.0")


if __name__ == "__main__":
    unittest.main()
