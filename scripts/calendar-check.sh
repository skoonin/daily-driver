#!/usr/bin/env bash
# Check if a named calendar exists in macOS Calendar.app
set -euo pipefail

cal_name="${1:?Usage: calendar-check.sh 'Calendar Name'}"

if osascript -e "tell application \"Calendar\" to get name of calendars" 2>/dev/null | tr ',' '\n' | sed 's/^ *//;s/ *$//' | grep -qxF "$cal_name"; then
  echo "Calendar '$cal_name': EXISTS"
elif ! osascript -e "tell application \"Calendar\" to get name of calendars" >/dev/null 2>&1; then
  echo "WARNING: Calendar app is not accessible -- check Privacy > Calendars in System Settings" >&2
else
  echo "Calendar '$cal_name': will be created on first /day-start"
fi
