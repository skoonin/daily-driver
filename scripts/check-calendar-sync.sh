#!/usr/bin/env bash
set -euo pipefail

# Checks that the plan calendar exists in macOS Calendar
# Usage: bash scripts/check-calendar-sync.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq not installed"
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: config.yaml not found at ${CONFIG}"
  exit 1
fi

if ! CALENDAR_NAME=$(yq '.calendar.plan_calendar_name // "Daily Plan"' "$CONFIG" 2>&1); then
  echo "ERROR: check-calendar-sync: could not read plan_calendar_name from config: ${CALENDAR_NAME}" >&2
  exit 1
fi
bash "${SCRIPT_DIR}/calendar-check.sh" "$CALENDAR_NAME"
