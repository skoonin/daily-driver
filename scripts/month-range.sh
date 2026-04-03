#!/usr/bin/env bash
set -euo pipefail

# Outputs current (or last) month's date range
# Usage: bash scripts/month-range.sh [--last]

LAST=false
if [[ "${1:-}" == "--last" ]]; then
  LAST=true
fi

if [[ "$LAST" == "true" ]]; then
  YEAR=$(date -v-1m +%Y)
  MONTH=$(date -v-1m +%m)
  MONTH_NAME=$(date -v-1m +%B)
  FIRST_DAY="${YEAR}-${MONTH}-01"
  LAST_DAY=$(date -j -v1d -v-1d +%Y-%m-%d)
  echo "last_month: year=${YEAR} month=${MONTH} name=${MONTH_NAME} first=${FIRST_DAY} last=${LAST_DAY}"
else
  YEAR=$(date +%Y)
  MONTH=$(date +%m)
  MONTH_NAME=$(date +%B)
  FIRST_DAY="${YEAR}-${MONTH}-01"
  LAST_DAY=$(date -j -v1d -v+1m -v-1d +%Y-%m-%d)
  echo "year=${YEAR} month=${MONTH} name=${MONTH_NAME} first=${FIRST_DAY} last=${LAST_DAY}"
fi
