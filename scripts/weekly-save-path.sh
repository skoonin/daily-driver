#!/usr/bin/env bash
set -euo pipefail

# Creates the weekly summaries directory and outputs the save path for this week's summary
# Usage: bash scripts/weekly-save-path.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi
if ! SAVE_DIR=$(yq '.reporting.week.save_dir // "weekly"' "$CONFIG" 2>&1); then
  echo "ERROR: weekly-save-path: could not read save_dir from config: ${SAVE_DIR}" >&2
  exit 1
fi
YEAR=$(date +%Y)
WEEK_NUM=$(date +%V)

mkdir -p "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}"
echo "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}/${YEAR}-W${WEEK_NUM}-week.md"
