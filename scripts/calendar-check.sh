#!/usr/bin/env bash
# Check if a named calendar exists in macOS Calendar.app
set -euo pipefail

cal_name="${1:?Usage: calendar-check.sh 'Calendar Name'}"

if osascript -e "tell application \"Calendar\" to get name of calendars" 2>&1 | grep -q "$cal_name"; then
  echo "Calendar '$cal_name': EXISTS"
else
  echo "Calendar '$cal_name': will be created on first /day-start"
fi
