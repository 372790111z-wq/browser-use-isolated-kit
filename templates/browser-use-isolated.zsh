#!/bin/zsh
set -euo pipefail

readonly BROWSER_USE_BIN="@@BROWSER_USE_BIN@@"
readonly CHROME_APP="@@CHROME_APP@@"
readonly CHROME_BIN="@@CHROME_BIN@@"
readonly PROFILE_DIR="@@PROFILE_DIR@@"
readonly AGENT_WORKSPACE="@@AGENT_WORKSPACE@@"
readonly POOL_STATE="$AGENT_WORKSPACE/tab-pool/state.json"
readonly POOL_SIZE=@@POOL_SIZE@@
readonly HARD_LIMIT=$((POOL_SIZE * 2))
readonly INITIAL_TABS=1
readonly CDP_PORT=@@CDP_PORT@@
readonly CDP_URL="http://127.0.0.1:$CDP_PORT"

typeset thread_id="${CODEX_THREAD_ID:-shared}"
if [[ ! "$thread_id" =~ '^[A-Za-z0-9_-]{1,40}$' ]]; then
  thread_id="shared"
fi
readonly DAEMON_NAME="codex-isolated-$thread_id"
readonly DAEMON_GENERATION_FILE="$AGENT_WORKSPACE/tab-pool/daemon-$thread_id.generation"

if [[ ! -x "$BROWSER_USE_BIN" ]]; then
  print -u2 "browser-use-isolated: browser-use is not installed at $BROWSER_USE_BIN"
  exit 1
fi

if [[ ! -x "$CHROME_BIN" ]]; then
  print -u2 "browser-use-isolated: Google Chrome is not installed at $CHROME_BIN"
  exit 1
fi

/bin/mkdir -p "$PROFILE_DIR" "$AGENT_WORKSPACE/tab-pool"

typeset started_chrome=0

listener_pids() {
  /usr/sbin/lsof -t -nP -iTCP:"$CDP_PORT" -sTCP:LISTEN 2>/dev/null \
    | /usr/bin/sort -u
}

port_has_listener() {
  [[ -n "$(listener_pids)" ]]
}

cdp_is_ours() {
  /usr/bin/curl -fsS --max-time 1 "$CDP_URL/json/version" >/dev/null 2>&1 \
    || return 1
  typeset pid command found_ours=0
  for pid in $(listener_pids); do
    command=$(/bin/ps -p "$pid" -o command= 2>/dev/null || true)
    if [[ "$command" == *"--user-data-dir=$PROFILE_DIR"* \
        && "$command" == *"--remote-debugging-port=$CDP_PORT"* ]]; then
      found_ours=1
    else
      return 1
    fi
  done
  [[ "$found_ours" -eq 1 ]]
}

has_page_target() {
  /usr/bin/curl -fsS --max-time 1 "$CDP_URL/json/list" 2>/dev/null \
    | /usr/bin/grep -Eq '"type"[[:space:]]*:[[:space:]]*"page"'
}

pool_marker_count() {
  /usr/bin/curl -fsS --max-time 1 "$CDP_URL/json/list" 2>/dev/null \
    | /usr/bin/jq -r '[.[] | select(.type == "page" and (.url | startswith("about:blank#codex-pool-")))] | length'
}

browser_generation() {
  /usr/bin/curl -fsS --max-time 1 "$CDP_URL/json/version" 2>/dev/null \
    | /usr/bin/jq -r '.webSocketDebuggerUrl // "" | split("/")[-1]'
}

state_generation() {
  if [[ -f "$POOL_STATE" ]]; then
    /usr/bin/jq -r '.generation // ""' "$POOL_STATE" 2>/dev/null || true
  fi
}

state_pool_size() {
  if [[ -f "$POOL_STATE" ]]; then
    /usr/bin/jq -r '.pool_size // 0' "$POOL_STATE" 2>/dev/null || true
  fi
}

non_disposable_page_count() {
  /usr/bin/curl -fsS --max-time 1 "$CDP_URL/json/list" 2>/dev/null \
    | /usr/bin/jq -r '[.[] | select(.type == "page") | select(((((.url // "") | startswith("about:blank")) or (((.url // "") | startswith("chrome://newtab"))) or (((.url // "") | startswith("chrome://new-tab-page"))) or (((.url // "") | startswith("data:text/html,BU_POOL_")))) | not))] | length'
}

terminate_isolated_chrome() {
  typeset isolated_pid
  isolated_pid=$(
    /usr/bin/pgrep -f -- "--user-data-dir=$PROFILE_DIR" \
      | /usr/bin/head -n 1 || true
  )
  if [[ -z "$isolated_pid" ]]; then
    return
  fi
  /bin/kill -TERM "$isolated_pid"
  for attempt in {1..30}; do
    if ! cdp_is_ours; then
      break
    fi
    /bin/sleep 0.2
  done
}

frontmost_pid() {
  /usr/bin/osascript -l JavaScript \
    -e 'ObjC.import("AppKit"); console.log(ObjC.unwrap($.NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier))' \
    2>/dev/null || true
}

restore_frontmost_pid() {
  typeset pid="$1"
  if [[ ! "$pid" =~ '^[0-9]+$' ]]; then
    return
  fi
  /usr/bin/osascript -l JavaScript \
    -e 'ObjC.import("AppKit"); function run(argv) { const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(parseInt(argv[0], 10)); if (app) app.activateWithOptions(3); }' \
    "$pid" >/dev/null 2>&1 || true
}

bootstrap_pool() {
  typeset previous_front_pid result_code=0
  previous_front_pid="$(frontmost_pid)"
  if ! (
    export BU_NAME="codex-pool-bootstrap"
    export BU_CDP_URL="$CDP_URL"
    export BH_AGENT_WORKSPACE="/private/tmp/browser-use-pool-bootstrap"
    "$BROWSER_USE_BIN" --reload >/dev/null 2>&1 || true
    "$BROWSER_USE_BIN" <<PY >/dev/null
targets = [
    t for t in cdp("Target.getTargets")["targetInfos"]
    if t.get("type") == "page" and t.get("targetId")
]
by_url = {t.get("url"): t.get("targetId") for t in targets}
reusable = [
    t.get("targetId") for t in targets
    if (t.get("url") or "") in ("", "about:blank")
    or (t.get("url") or "").startswith("chrome://newtab")
    or (t.get("url") or "").startswith("chrome://new-tab-page")
    or (t.get("url") or "").startswith("data:text/html,BU_POOL_")
]
for slot in range(1, ${INITIAL_TABS} + 1):
    url = f"about:blank#codex-pool-{slot:02d}"
    target_id = by_url.get(url)
    needs_navigation = False
    if not target_id and reusable:
        target_id = reusable.pop(0)
        needs_navigation = True
    if not target_id:
        raise RuntimeError(
            "Not enough existing blank tabs to initialize the pool; restart "
            "the isolated Chrome so the wrapper can preallocate them"
        )
    session_id = cdp(
        "Target.attachToTarget", targetId=target_id, flatten=True
    )["sessionId"]
    if needs_navigation:
        cdp("Page.navigate", session_id=session_id, url=url)
    title = f"空闲 {slot:02d}"
    expression = (
        "(()=>{"
        f"if(location.href!=={url!r})return false;"
        f"document.title={title!r};"
        "if(document.body)document.body.replaceChildren();"
        "return true;})()"
    )
    for _ in range(20):
        result = cdp(
            "Runtime.evaluate",
            session_id=session_id,
            expression=expression,
            returnByValue=True,
        )
        if result.get("result", {}).get("value") is True:
            break
        time.sleep(0.05)
    cdp("Target.detachFromTarget", sessionId=session_id)
for target_id in reusable:
    cdp("Target.closeTarget", targetId=target_id)
PY
    "$BROWSER_USE_BIN" --reload >/dev/null 2>&1 || true
  ); then
    result_code=1
  fi
  restore_frontmost_pid "$previous_front_pid"
  return "$result_code"
}

# Chrome can keep its background process and CDP port alive after its last
# window closes. Restart that empty isolated process so LaunchServices can
# create the work page in the background without using Target.createTarget.
if cdp_is_ours \
    && ! has_page_target; then
  terminate_isolated_chrome
fi

# Self-heal after Chrome updates or external restarts when only disposable
# blank pages exist. Real pages are never closed automatically.
if cdp_is_ours; then
  typeset current_generation saved_generation_before saved_pool_size_before marker_count_before real_page_count
  current_generation="$(browser_generation)"
  saved_generation_before="$(state_generation)"
  saved_pool_size_before="$(state_pool_size)"
  marker_count_before="$(pool_marker_count)"
  if [[ "$saved_generation_before" != "$current_generation" \
      || "${saved_pool_size_before:-0}" -ne "$POOL_SIZE" ]]; then
    if [[ "$marker_count_before" -lt "$INITIAL_TABS" ]]; then
      real_page_count="$(non_disposable_page_count)"
      if [[ "$real_page_count" -eq 0 ]]; then
        terminate_isolated_chrome
      else
        print -u2 "browser-use-isolated: preserving and adopting $real_page_count restored page(s)"
      fi
    fi
  fi
fi

if ! cdp_is_ours; then
  if port_has_listener; then
    print -u2 "browser-use-isolated: port $CDP_PORT is occupied by another application"
    print -u2 "choose a different port with the Browser Use Isolated Kit installer"
    exit 1
  fi
  started_chrome=1
  typeset -a pool_urls
  typeset slot
  for (( slot=1; slot<=INITIAL_TABS; slot++ )); do
    pool_urls+=("data:text/html,BU_POOL_$(printf '%02d' "$slot")")
  done
  /usr/bin/open -g -na "$CHROME_APP" --args \
    --user-data-dir="$PROFILE_DIR" \
    --remote-debugging-port="$CDP_PORT" \
    --disable-background-timer-throttling \
    --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    --no-first-run \
    --no-default-browser-check \
    --new-window \
    "${pool_urls[@]}"

  for attempt in {1..50}; do
    if cdp_is_ours \
        && has_page_target; then
      break
    fi
    /bin/sleep 0.2
  done
fi

if ! cdp_is_ours \
    || ! has_page_target; then
  print -u2 "browser-use-isolated: Chrome did not expose a page target at $CDP_URL"
  exit 1
fi

typeset generation
generation="$(browser_generation)"
if [[ -z "$generation" ]]; then
  print -u2 "browser-use-isolated: could not identify the Chrome generation"
  exit 1
fi

typeset saved_generation saved_pool_size marker_count
saved_generation="$(state_generation)"
saved_pool_size="$(state_pool_size)"
marker_count="$(pool_marker_count)"
if [[ ("$saved_generation" != "$generation" || "${saved_pool_size:-0}" -ne "$POOL_SIZE") \
    && "$marker_count" -lt "$INITIAL_TABS" ]]; then
  typeset final_real_page_count
  final_real_page_count="$(non_disposable_page_count)"
  if [[ "$final_real_page_count" -eq 0 ]]; then
    if ! bootstrap_pool; then
      print -u2 "browser-use-isolated: failed to initialize the first managed tab"
      exit 1
    fi
    marker_count="$(pool_marker_count)"
  else
    print -u2 "browser-use-isolated: preserving and adopting $final_real_page_count restored page(s)"
  fi
fi

export BU_NAME="$DAEMON_NAME"
export BU_CDP_URL="$CDP_URL"
export BH_AGENT_WORKSPACE="$AGENT_WORKSPACE"
export BU_BROWSER_GENERATION="$generation"
export BU_TAB_POOL_SIZE="$POOL_SIZE"
export BU_TAB_POOL_HARD_LIMIT="$HARD_LIMIT"
export BROWSER_USE_BACKGROUND_TABS=1

# Each Codex thread keeps its daemon between invocations so helpers such as
# page_info() remain attached to that thread's tab. Reconnect only when the
# underlying Chrome browser generation changes.
typeset daemon_generation=""
if [[ -f "$DAEMON_GENERATION_FILE" ]]; then
  daemon_generation="$(<"$DAEMON_GENERATION_FILE")"
fi
if [[ "$daemon_generation" != "$generation" ]]; then
  "$BROWSER_USE_BIN" --reload >/dev/null 2>&1 || true
  print -r -- "$generation" >| "$DAEMON_GENERATION_FILE"
fi

exec "$BROWSER_USE_BIN" "$@"
