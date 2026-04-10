#!/usr/bin/env bash
set -euo pipefail

# Outputs today's calendar events for Claude consumption
# Requires: icalBuddy (brew install ical-buddy)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

if ! command -v icalBuddy &>/dev/null; then
  echo "ERROR: icalBuddy not installed. Run: brew install ical-buddy"
  exit 1
fi

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq not installed"
  exit 1
fi

# Build -ec args if excluded_calendars is non-empty in config
EC_ARGS=()
if [[ -f "$CONFIG" ]]; then
  if ! excluded_count=$(yq '.calendar.excluded_calendars | length' "$CONFIG" 2>&1); then
    echo "WARNING: could not read excluded_calendars from config: ${excluded_count}" >&2
    excluded_count="0"
  fi
  if [[ "$excluded_count" -gt 0 ]]; then
    if ! excluded_list=$(yq '.calendar.excluded_calendars | join(",")' "$CONFIG" 2>&1); then
      echo "WARNING: could not read excluded_calendars from config: ${excluded_list}" >&2
      excluded_list=""
    fi
    if [[ -n "$excluded_list" && "$excluded_list" != "null" ]]; then
      EC_ARGS=(-ec "$excluded_list")
    fi
  fi
fi

echo "=== Calendar: Today's Events ==="
icalBuddy -sd -nc -npn -iep "title,datetime,location,notes" -b "- " "${EC_ARGS[@]}" eventsToday || echo "(no events or calendar access denied)"
echo ""

echo "=== Calendar: Tomorrow's Events ==="
icalBuddy -sd -nc -npn -iep "title,datetime,location" -b "- " "${EC_ARGS[@]}" eventsFrom:"tomorrow" to:"tomorrow 23:59" || echo "(unable to fetch)"
