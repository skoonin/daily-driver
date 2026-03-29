#!/usr/bin/env bash
set -euo pipefail

# Launched by launchd at 4pm weekdays
# Opens iTerm2 with Claude in daily-driver dir, running /day-end

DAILY_DRIVER="$HOME/git/daily-driver"

if ! command -v claude &>/dev/null; then
  osascript -e 'display notification "claude CLI not found" with title "Daily Driver"'
  exit 1
fi

# Open iTerm2 with a new tab, cd to daily-driver, launch claude
osascript <<'APPLESCRIPT'
tell application "iTerm"
  activate
  tell current window
    create tab with default profile
    tell current session
      write text "cd ~/git/daily-driver && claude"
    end tell
  end tell
end tell
APPLESCRIPT
