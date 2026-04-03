#!/usr/bin/env bash
set -euo pipefail

# Checks that the plan calendar exists in macOS Calendar
# Usage: bash scripts/check-calendar-sync.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

CALENDAR_NAME=$(yq '.calendar.plan_calendar_name // "Daily Plan"' "$CONFIG")
bash "${SCRIPT_DIR}/calendar-check.sh" "$CALENDAR_NAME"
