# Browser Use Isolated Kit

Turn Browser Use on macOS into a maintainable isolated browser environment
instead of a one-time prompt-generated setup.

The kit installs:

- a dedicated Chrome profile and CDP endpoint;
- a background-safe `browser-use-isolated` wrapper;
- a per-Codex-thread observable 12-tab pool;
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

## Remove managed files

```bash
python3 scripts/manage.py uninstall --yes
```

Uninstall preserves the dedicated Chrome profile. Profile deletion is never
automated.

## One-step Codex prompt

See [EXECUTE-PROMPT.md](EXECUTE-PROMPT.md). The prompt delegates implementation
to the versioned repository instead of duplicating scripts inside an article.
