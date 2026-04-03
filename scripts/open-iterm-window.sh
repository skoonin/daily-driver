#!/usr/bin/env bash
# Opens a new iTerm2 window and runs the given command
set -euo pipefail

cmd="${1:?Usage: open-iterm-window.sh 'command to run'}"

osascript -e 'tell application "iTerm"' \
  -e 'activate' \
  -e 'set newWindow to (create window with default profile)' \
  -e "tell current session of newWindow" \
  -e "write text \"$cmd\"" \
  -e 'end tell' \
  -e 'end tell'
