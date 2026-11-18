# Architecture

The isolated environment uses the installed Google Chrome application but does
not share the user's everyday browser state.

```text
Google Chrome.app
└── dedicated process
    ├── dedicated user-data-dir
    ├── dedicated persisted CDP port
    ├── shared on-demand tab workspace
    └── one Browser Harness daemon namespace per Codex thread
```

The repository maintains four layers:

1. `browser-use-isolated` renders device paths and exports the isolation
   environment.
2. `agent_helpers.py` exposes all isolated tabs, protects each active tab with
   a per-thread lease, and permits takeover only after completion or lease loss.
   Tabs are created on demand. The default soft threshold is 12 and the default
   safety limit is 24; capacity cleanup archives only completed or abandoned
   work and never steals a live lease.
   Chrome starts with `--no-startup-window`; after the browser-level CDP
   endpoint is ready, a managed helper creates one disposable bootstrap target
   with `background=true`. This avoids a visible cold-start window and is not a
   preallocated work-tab pool. Work pages are still created on demand.
3. Version-checked upstream patches prevent normal CDP tab selection from
   activating Chrome on macOS.
4. A marked policy block in the official Browser Use Skill forces local tasks
   through the isolated wrapper and forbids silent fallback.

An optional fifth layer registers the pinned official Chrome DevTools MCP as a
separate Codex server named `browser-use-isolated`. Its wrapper starts the
dedicated Chrome when necessary and connects the MCP to the persisted CDP URL.
This avoids model-generated task shells needing process-inspection or localhost
permissions. It does not replace Browser Use's lease manager: independent MCP
tasks create new background pages, and cross-session continuation remains an
explicit routing decision.
Its stdio guard forces page creation and selection to remain in the background,
even when an Agent omits the upstream optional flags or requests foreground
defaults. Visual foreground activation remains an explicit user-controlled
Browser Use action.
The guard also defers Chrome startup until the first `tools/call`. A file lock
prevents concurrent MCP sessions from racing to launch the same Profile and
port. On macOS the wrapper executes the Chrome binary directly with the custom
user-data-dir instead of asking LaunchServices to create another
`com.google.Chrome` application instance through `open -na`.

An upstream Browser Use upgrade can replace layers 3 and 4. The `update
--upstream` command upgrades first and then reapplies all managed layers. If an
expected source anchor changed, the installer stops instead of guessing.

At first install the kit chooses an unused port starting at `9333`, then writes
it to device-local configuration. Every IDE discovers the same endpoint through
the wrapper; the port does not change per launch or per AI. A custom port can be
supplied with `--port`. Multiple isolated environments require separate profile
and port pairs and are not created implicitly.

If Chrome remains alive, another task reconnects to the original target. After
a browser crash, the persistent profile recovers cookies and site storage while
the tab archive provides URL, title, owner, reason, and timestamp. Web-memory
state and unsubmitted third-party forms cannot be guaranteed after a process
crash, so consequential actions must be verified before replay.
