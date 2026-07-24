# Architecture

The isolated environment uses the installed Google Chrome application but does
not share the user's everyday browser state.

```text
Google Chrome.app
└── dedicated process
    ├── dedicated user-data-dir
    ├── dedicated CDP port
    ├── observable 12-tab pool
    └── one Browser Harness daemon namespace per Codex thread
```

The repository maintains four layers:

1. `browser-use-isolated` renders device paths and exports the isolation
   environment.
2. `agent_helpers.py` assigns tabs by Codex thread, preserves completed pages,
   and safely reclaims completed slots when the pool is full.
3. Version-checked upstream patches prevent normal CDP tab selection from
   activating Chrome on macOS.
4. A marked policy block in the official Browser Use Skill forces local tasks
   through the isolated wrapper and forbids silent fallback.

An upstream Browser Use upgrade can replace layers 3 and 4. The `update
--upstream` command upgrades first and then reapplies all managed layers. If an
expected source anchor changed, the installer stops instead of guessing.

The default CDP port is `9333` because Codex/DeepWorks desktop environments may
already listen on `9223`. A custom port can be supplied with `--port`; all
generated files then use the same value.
