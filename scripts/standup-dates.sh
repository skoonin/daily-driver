#!/usr/bin/env bash
set -euo pipefail

# Outputs today's date, day-of-week, yesterday (business day), and standup lookback start
# Usage: bash scripts/standup-dates.sh

TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)

case "$DOW" in
  1) YESTERDAY=$(date -v-3d +%Y-%m-%d); LOOKBACK_START=$(date -v-3d +%Y-%m-%d) ;;
  2) YESTERDAY=$(date -v-1d +%Y-%m-%d); LOOKBACK_START=$(date -v-1d +%Y-%m-%d) ;;
  3) YESTERDAY=$(date -v-1d +%Y-%m-%d); LOOKBACK_START=$(date -v-2d +%Y-%m-%d) ;;
  4) YESTERDAY=$(date -v-1d +%Y-%m-%d); LOOKBACK_START=$(date -v-1d +%Y-%m-%d) ;;
  5) YESTERDAY=$(date -v-1d +%Y-%m-%d); LOOKBACK_START=$(date -v-2d +%Y-%m-%d) ;;
  *) YESTERDAY=$(date -v-1d +%Y-%m-%d); LOOKBACK_START=$(date -v-1d +%Y-%m-%d) ;;
esac

echo "today=${TODAY} dow=${DOW} yesterday=${YESTERDAY} lookback_start=${LOOKBACK_START}"
