#!/usr/bin/env bash
set -euo pipefail

# Outputs today's calendar events for Claude consumption
# Requires: icalBuddy (brew install ical-buddy)

if ! command -v icalBuddy &>/dev/null; then
  echo "ERROR: icalBuddy not installed. Run: brew install ical-buddy"
  exit 1
fi

echo "=== Calendar: Today's Events ==="
icalBuddy -sd -nc -npn -iep "title,datetime,location,notes" -b "- " eventsToday 2>/dev/null || echo "(no events or calendar access denied)"
echo ""

echo "=== Calendar: Tomorrow's Events ==="
icalBuddy -sd -nc -npn -iep "title,datetime,location" -b "- " eventsFrom:"tomorrow" to:"tomorrow 23:59" 2>/dev/null || echo "(unable to fetch)"
