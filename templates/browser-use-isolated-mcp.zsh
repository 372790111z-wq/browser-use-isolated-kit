#!/bin/zsh
set -euo pipefail

readonly BROWSER_WRAPPER="@@BROWSER_WRAPPER@@"
readonly MCP_BIN="@@MCP_BIN@@"
readonly MCP_GUARD="@@MCP_GUARD@@"
readonly CDP_URL="http://127.0.0.1:@@CDP_PORT@@"
readonly PROFILE_DIR="@@PROFILE_DIR@@"
readonly LOG_DIR="@@LOG_DIR@@"
readonly LOG_FILE="$LOG_DIR/mcp-bootstrap.log"

/bin/mkdir -p "$LOG_DIR"

# Starting an MCP server must not start Chrome. The stdio guard launches the
# isolated browser lazily immediately before the first browser tool call.
export BROWSER_USE_ISOLATED_WRAPPER="$BROWSER_WRAPPER"
export BROWSER_USE_ISOLATED_CDP_URL="$CDP_URL"
export BROWSER_USE_ISOLATED_LOG_FILE="$LOG_FILE"
export BROWSER_USE_ISOLATED_PROFILE_DIR="$PROFILE_DIR"

exec /usr/bin/python3 "$MCP_GUARD" "$MCP_BIN" \
  "--browser-url=$CDP_URL" \
  --no-usage-statistics
