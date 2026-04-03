#!/usr/bin/env bash
set -euo pipefail

# Commits this month's summary file in the output directory
# Usage: bash scripts/commit-monthly.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
YEAR=$(date +%Y)
MONTH=$(date +%m)
MONTH_NAME=$(date +%B)

git -C "$OUTPUT_DIR" add -A && git -C "$OUTPUT_DIR" commit -m "monthly summary: ${YEAR}-${MONTH} ${MONTH_NAME}" 2>/dev/null || echo "(nothing to commit)"
