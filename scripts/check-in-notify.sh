#!/usr/bin/env bash
set -euo pipefail

# Check-in trigger called by launchd every 30 minutes
# Gates: weekends, work hours, focus lock. Opens iTerm2 in background.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
STATE_DIR="$HOME/.local/share/daily-driver"
LOCK_FILE="${STATE_DIR}/focus.lock"

# Ensure PATH includes Homebrew and local bins for launchd context
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  [[ -d "$dir" ]] && export PATH="${dir}:${PATH}"
done

# Opens iTerm2 with claude running /check-in in the daily-driver directory
# Falls back to Terminal.app if iTerm2 is not available
open_checkin() {
  local repo_dir
  repo_dir="$(cd "${SCRIPT_DIR}/.." && pwd)"

  if [ -d /Applications/iTerm.app ]; then
    osascript <<EOF
tell application "iTerm"
  set newWindow to (create window with default profile)
  tell current session of newWindow
    write text "cd '${repo_dir}' && claude --model sonnet --effort medium --agent work-planner -n 'check-in-$(date +%Y-%m-%d-%H%M)' '/check-in'"
  end tell
end tell
EOF
  else
    osascript <<EOF
tell application "Terminal"
  do script "cd '${repo_dir}' && claude --model sonnet --effort medium --agent work-planner -n 'check-in-$(date +%Y-%m-%d-%H%M)' '/check-in'"
end tell
EOF
  fi
}

# Gate 1: Weekend
dow=$(date +%u)
if [[ "$dow" -ge 6 ]]; then
  exit 0
fi

# Gate 2: Outside work hours
current_hour=$(date +%H)
current_min=$(date +%M)
current_minutes=$((10#$current_hour * 60 + 10#$current_min))

if command -v yq &>/dev/null && [[ -f "$CONFIG" ]]; then
  start_time=$(yq '.checkin.work_hours.start // "09:00"' "$CONFIG")
  end_time=$(yq '.checkin.work_hours.end // "17:00"' "$CONFIG")
else
  start_time="09:00"
  end_time="17:00"
fi

start_h="${start_time%%:*}"
start_m="${start_time##*:}"
start_minutes=$((10#$start_h * 60 + 10#$start_m))

end_h="${end_time%%:*}"
end_m="${end_time##*:}"
end_minutes=$((10#$end_h * 60 + 10#$end_m))

if [[ "$current_minutes" -lt "$start_minutes" || "$current_minutes" -ge "$end_minutes" ]]; then
  exit 0
fi

# Gate 3: Focus lock active
if [[ -f "$LOCK_FILE" ]]; then
  if ! command -v jq &>/dev/null; then
    # Cannot parse lock -- treat as active to avoid interrupting focus
    exit 0
  fi
  end_epoch=$(jq -r '.end_epoch // 0' "$LOCK_FILE" 2>/dev/null) || end_epoch=0
  now_epoch=$(date +%s)
  if [[ "$now_epoch" -lt "$end_epoch" ]]; then
    exit 0
  fi
  # Expired lock, remove it
  rm -f "$LOCK_FILE"
fi

# All gates passed -- open iTerm2 with /check-in
open_checkin || echo "$(date): open_checkin failed (exit $?)" >> "${STATE_DIR}/check-in-notify.log"
