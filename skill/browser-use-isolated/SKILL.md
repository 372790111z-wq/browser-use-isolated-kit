---
name: browser-use-isolated
description: "Install, diagnose, upgrade, or remove the macOS Browser Use isolated environment and its Codex MCP entrypoint, with a dedicated Chrome profile, persisted CDP endpoint, shared on-demand tabs, background-focus protection, and no fallback to everyday Chrome. Use when setting up Browser Use on a new Mac, repairing isolated startup, syncing this kit across machines, configuring all Codex sessions to use the same isolated Chrome, or upgrading Browser Use without losing local isolation patches."
---

# Browser Use Isolated

Use the repository installer instead of recreating the configuration from a
long prompt.

## Workflow

1. Confirm the repository root that contains `scripts/manage.py`.
2. Run static diagnosis before changing the machine:

   ```bash
   python3 scripts/manage.py doctor
   ```

3. For a first installation, verify Browser Use and Google Chrome exist, then
   run:

   ```bash
   python3 scripts/manage.py install
   ```

4. For a kit-only refresh, run:

   ```bash
   git pull --ff-only
   python3 scripts/manage.py update
   ```

5. Upgrade upstream Browser Use only when the user requests it:

   ```bash
   python3 scripts/manage.py update --upstream
   ```

6. Run static diagnosis again. Run live diagnosis only when starting or
   attaching to the isolated Chrome is acceptable:

   ```bash
   python3 scripts/manage.py doctor --live
   ```

7. To make the isolated browser available through host-managed MCP instead of
   each task shell, configure the pinned official Chrome DevTools MCP:

   ```bash
   python3 scripts/manage.py configure-mcp
   python3 scripts/manage.py doctor --mcp
   ```

   Existing Codex sessions must be restarted before the new tools appear.

8. Manage future MCP releases through the kit, never `@latest`:

   ```bash
   python3 scripts/manage.py check-mcp-update
   python3 scripts/manage.py update-mcp
   python3 scripts/manage.py rollback-mcp --yes
   ```

   `check-mcp-update` is read-only. `update-mcp` stages and validates the new
   executable before swapping it in and preserves one rollback copy.

## Safety

- Never copy, inspect, publish, or remove the dedicated Chrome profile.
- Never read or fall back to the user's everyday Chrome.
- Preserve original errors when diagnosis fails.
- Stop if package patch anchors do not match the installed upstream version.
- Do not claim success until the wrapper, Skill policy, helper, package
  patches, and doctor checks pass.
- `uninstall` requires `--yes` and preserves the Chrome profile.
- Never steal a live tab lease. Cross-session takeover is allowed only for an
  idle, completed, or abandoned tab.
- Do not hardcode a CDP port in prompts. The installer chooses it once and the
  wrapper exposes the persisted endpoint to every supported IDE.
- Normal browser tasks use the `browser-use-isolated` MCP server. Direct CLI
  access is maintenance or an explicitly approved compatibility fallback.
- Pin the official Chrome DevTools MCP version; never configure `@latest`.
- MCP connection success does not prove concurrent lease isolation. Create a
  new background page per independent task and test multi-session behavior.
- The MCP guard forces `new_page` into the background and prevents
  `select_page` from bringing Chrome forward. Only an explicit user request to
  show the browser may use the gated Browser Use `show_tab()` helper.

Read `references/architecture.md` when explaining the isolation boundary or
debugging an upgrade.
