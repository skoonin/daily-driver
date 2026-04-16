#!/usr/bin/env bash
set -euo pipefail

# Outputs current (or last) ISO week Monday and Friday dates
# Usage: bash scripts/week-range.sh [--last]

LAST=false
if [[ "${1:-}" == "--last" ]]; then
  LAST=true
fi

DOW=$(date +%u)
DAYS_SINCE_MON=$((DOW - 1))
WEEK_NUM=$(date +%V)
YEAR=$(date +%G)

if [[ "$LAST" == "true" ]]; then
  LAST_MON=$(date -j -v-${DAYS_SINCE_MON}d -v-7d +%Y-%m-%d)
  LAST_FRI=$(date -j -v-${DAYS_SINCE_MON}d -v-3d +%Y-%m-%d)
  echo "last_week_monday=${LAST_MON} last_week_friday=${LAST_FRI}"
else
  WEEK_MON=$(date -j -v-${DAYS_SINCE_MON}d +%Y-%m-%d)
  WEEK_FRI=$(date -j -v-${DAYS_SINCE_MON}d -v+4d +%Y-%m-%d)
  echo "week=W${WEEK_NUM} monday=${WEEK_MON} friday=${WEEK_FRI} dow=${DOW}"
fi
