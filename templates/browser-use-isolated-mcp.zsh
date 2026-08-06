#!/bin/zsh
set -euo pipefail

readonly BROWSER_WRAPPER="@@BROWSER_WRAPPER@@"
readonly MCP_BIN="@@MCP_BIN@@"
readonly CDP_URL="http://127.0.0.1:@@CDP_PORT@@"
readonly LOG_DIR="@@LOG_DIR@@"
readonly LOG_FILE="$LOG_DIR/mcp-bootstrap.log"

/bin/mkdir -p "$LOG_DIR"

# Codex launches this through its MCP host instead of a model-generated task
# shell. Start the dedicated browser only when its persisted CDP endpoint is
# not already responding.
if ! /usr/bin/curl -fsS --max-time 1 "$CDP_URL/json/version" >/dev/null 2>&1; then
  CODEX_THREAD_ID=mcp-bootstrap "$BROWSER_WRAPPER" >"$LOG_FILE" 2>&1 <<'PY'
print(tab_pool_status())
PY
fi

if ! /usr/bin/curl -fsS --max-time 2 "$CDP_URL/json/version" >/dev/null 2>&1; then
  print -u2 "browser-use-isolated-mcp: dedicated Chrome is unavailable at $CDP_URL"
  print -u2 "browser-use-isolated-mcp: inspect $LOG_FILE"
  exit 1
fi

exec "$MCP_BIN" \
  "--browser-url=$CDP_URL" \
  --no-usage-statistics
