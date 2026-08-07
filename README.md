# Browser Use Isolated Kit

Turn Browser Use on macOS into a maintainable isolated browser environment
instead of a one-time prompt-generated setup.

The kit installs:

- a dedicated Chrome profile and CDP endpoint;
- a background-safe `browser-use-isolated` wrapper;
- an observable shared tab workspace with per-task leases;
- an optional host-managed Codex MCP entrypoint pinned to Chrome DevTools MCP
  `1.6.0`;
- on-demand tabs, a configurable soft threshold, and safe automatic archival;
- deterministic Browser Use compatibility patches;
- an idempotent policy block in the installed Browser Use Skill;
- install, update, doctor, and safe uninstall commands.

It does not contain or upload cookies, browser history, login state, passwords,
tokens, or Chrome profiles.

## Prerequisites

- macOS
- Google Chrome in `/Applications`
- Python 3.9 or newer
- `browser-use` installed through its current official installation method
- `curl`, `jq`, `lsof`, and `zsh`

## Install

```bash
git clone https://github.com/372790111z-wq/browser-use-isolated-kit.git
cd browser-use-isolated-kit
python3 scripts/manage.py doctor
python3 scripts/manage.py install
python3 scripts/manage.py doctor --live
```

The live doctor may start or attach to the isolated Chrome. If Chrome asks for
remote-debugging permission, approve it manually and rerun the command.

## Upgrade

Refresh only this kit:

```bash
git pull --ff-only
python3 scripts/manage.py update
```

Upgrade Browser Use and reapply the isolation layers:

```bash
git pull --ff-only
python3 scripts/manage.py update --upstream
```

The upgrade stops if the installed Browser Use source is incompatible with the
known patches. It never silently switches to everyday Chrome.

## Use

```bash
browser-use-isolated <<'PY'
new_tab("https://example.com")
print(page_info())
complete_thread_tabs()
PY
```

## Configure Codex MCP

This avoids task-shell `ps` and localhost permission differences by connecting
the official Chrome DevTools MCP to the already-running dedicated Chrome:

```bash
python3 scripts/manage.py configure-mcp
python3 scripts/manage.py doctor --mcp
```

The command installs the pinned npm package under
`~/.local/share/browser-use-isolated-kit/mcp`, adds a separate
`browser-use-isolated` MCP entry, and does not replace an existing
`chrome-devtools` MCP configuration. Restart existing Codex sessions afterward.

The MCP provides a host-managed permission path, but it does not enforce the
Browser Use helper's per-thread leases. The installed policy therefore creates
a new background page for each independent task and continues an existing page
only when explicitly requested. Concurrent-session behavior must be validated
after configuration rather than inferred from successful connection alone.
The managed stdio guard overrides Chrome DevTools MCP's foreground defaults:
new pages always open in the background, page selection never activates the
Chrome window, and foreground display remains an explicit Browser Use action.
Loading the MCP server does not start Chrome. The guard starts it under a host
lock immediately before the first browser tool call, so ordinary Codex tasks
do not leave a second Chrome instance registered for URL handling.

To roll back only the MCP entry while preserving Browser Use and the Chrome
profile:

```bash
python3 scripts/manage.py remove-mcp --yes
```

Prerequisite: Node.js `^20.19.0`, `^22.12.0`, or `>=23`, plus npm.

## MCP lifecycle

Check the npm registry without modifying the installation:

```bash
python3 scripts/manage.py check-mcp-update
```

Upgrade to the latest release, or to an explicitly selected version:

```bash
python3 scripts/manage.py update-mcp
python3 scripts/manage.py update-mcp --to 1.7.0
```

The updater installs into a staging directory, checks the executable version
and required `--browser-url`/usage-statistics options, then swaps it into place.
The previous installation is preserved for one-step rollback:

```bash
python3 scripts/manage.py rollback-mcp --yes
```

The validated device version is persisted in local configuration, so a later
`configure-mcp` does not silently downgrade it to the repository default.

The first installation selects an unused local CDP port (starting at `9333`)
and persists it. Later launches never silently change ports. Opening more
ordinary Chrome windows does not affect this configuration.

The default soft threshold is 12 tabs and the default safety limit is 24.
Tabs are created only when needed. At capacity, only completed or abandoned
leases may be archived and reused; an actively leased page is preserved. All
sessions can list the isolated workspace, while `switch_tab()` takes over only
a completed, idle, or abandoned lease.

## Remove managed files

```bash
python3 scripts/manage.py uninstall --yes
```

Uninstall preserves the dedicated Chrome profile. Profile deletion is never
automated.

## One-step Codex prompt

See [EXECUTE-PROMPT.md](EXECUTE-PROMPT.md). The prompt delegates implementation
to the versioned repository instead of duplicating scripts inside an article.
