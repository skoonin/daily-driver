#!/usr/bin/env bash
set -euo pipefail

# Commits this week's summary file in the output directory
# Usage: bash scripts/commit-weekly.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
YEAR=$(date +%Y)
WEEK_NUM=$(date +%V)

git -C "$OUTPUT_DIR" add -A && git -C "$OUTPUT_DIR" commit -m "weekly summary: ${YEAR}-W${WEEK_NUM}" 2>/dev/null || echo "(nothing to commit)"
