#!/usr/bin/env bash
set -euo pipefail

# Commits this month's summary file in the output directory
# Usage: bash scripts/commit-monthly.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi
YEAR=$(date +%Y)
MONTH=$(date +%m)
MONTH_NAME=$(date +%B)

if ! git -C "$OUTPUT_DIR" add -A; then
  echo "ERROR: git add failed in ${OUTPUT_DIR}" >&2
  exit 1
fi
git -C "$OUTPUT_DIR" commit -m "monthly summary: ${YEAR}-${MONTH} ${MONTH_NAME}" || {
  if git -C "$OUTPUT_DIR" diff --cached --quiet 2>/dev/null; then
    echo "(nothing to commit)"
  else
    echo "ERROR: git commit failed in ${OUTPUT_DIR}" >&2
    exit 1
  fi
}
