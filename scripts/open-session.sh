#!/usr/bin/env bash
set -euo pipefail

# Open an iTerm2 (or Terminal) window running a daily-driver slash command.
# Called by launchd plists with the command as the first argument.
#
# Usage: open-session.sh <day-start|check-in|day-end>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="$HOME/.local/share/daily-driver"
LOCK_FILE="${STATE_DIR}/focus.lock"

COMMAND="${1:-check-in}"

# Ensure PATH includes Homebrew and local bins for launchd context
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  [[ -d "$dir" ]] && export PATH="${dir}:${PATH}"
done

open_session() {
  local repo_dir
  repo_dir="$(cd "${SCRIPT_DIR}/.." && pwd)"
  local session_name="${COMMAND}-$(date +%Y-%m-%d-%H%M)"
  local slash_cmd="/${COMMAND}"

  if [ -d /Applications/iTerm.app ]; then
    osascript <<EOF
tell application "iTerm"
  set newTab to (create tab with default profile)
  tell current session of newTab
    write text "cd '${repo_dir}' && claude --model sonnet --effort medium --agent work-planner -n '${session_name}' '${slash_cmd}'"
  end tell
end tell
EOF
  else
    osascript <<EOF
tell application "Terminal"
  do script "cd '${repo_dir}' && claude --model sonnet --effort medium --agent work-planner -n '${session_name}' '${slash_cmd}'"
end tell
EOF
  fi
}

# Gate 1: Weekends
dow=$(date +%u)
if [[ "$dow" -ge 6 ]]; then
  exit 0
fi

# Gate 2: Focus lock (check-in only — day-start and day-end always fire)
if [[ "$COMMAND" == "check-in" && -f "$LOCK_FILE" ]]; then
  if ! command -v jq &>/dev/null; then
    exit 0
  fi
  end_epoch=$(jq -r '.end_epoch // 0' "$LOCK_FILE" 2>/dev/null) || end_epoch=0
  now_epoch=$(date +%s)
  if [[ "$now_epoch" -lt "$end_epoch" ]]; then
    exit 0
  fi
  rm -f "$LOCK_FILE"
fi

mkdir -p "$STATE_DIR"
open_session || echo "$(date): open_session failed for ${COMMAND} (exit $?)" >> "${STATE_DIR}/open-session.log"
