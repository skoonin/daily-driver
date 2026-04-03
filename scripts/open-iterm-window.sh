#!/usr/bin/env bash
# Opens a new iTerm2 window (or tab in an existing session window) and runs the given command.
# Usage: open-iterm-window.sh [--dir PATH] 'command to run'
#
# Subsequent calls reuse the same iTerm2 window as tabs until that window is closed,
# then a new window is created automatically.
set -euo pipefail

# Persists window ID across invocations within the same login session
WID_FILE="/tmp/iterm-daily-driver-wid"

workdir=""
if [[ "${1:-}" == "--dir" ]]; then
  workdir="${2:?--dir requires a path}"
  shift 2
fi

cmd="${1:?Usage: open-iterm-window.sh [--dir PATH] 'command to run'}"

if [[ -n "$workdir" ]]; then
  cmd="cd '${workdir}' && ${cmd}"
fi

# Escape double quotes for AppleScript string embedding
as_cmd="${cmd//\"/\\\"}"

_open_new_window() {
  wid=$(osascript \
    -e 'tell application "iTerm"' \
    -e 'activate' \
    -e 'set w to (create window with default profile)' \
    -e "tell current session of w to write text \"${as_cmd}\"" \
    -e 'return (id of w) as string' \
    -e 'end tell')
  echo "$wid" > "$WID_FILE"
}

if [[ -f "$WID_FILE" ]]; then
  wid=$(cat "$WID_FILE")
  result=$(osascript \
    -e 'tell application "iTerm"' \
    -e 'activate' \
    -e 'try' \
    -e "  set w to window id ${wid}" \
    -e '  tell w' \
    -e '    create tab with default profile' \
    -e "    tell current session to write text \"${as_cmd}\"" \
    -e '  end tell' \
    -e '  return "ok"' \
    -e 'on error' \
    -e '  return "gone"' \
    -e 'end try' \
    -e 'end tell' 2>/dev/null || echo "gone")

  if [[ "$result" != "ok" ]]; then
    _open_new_window
  fi
else
  _open_new_window
fi
