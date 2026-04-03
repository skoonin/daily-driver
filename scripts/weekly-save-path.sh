#!/usr/bin/env bash
set -euo pipefail

# Creates the weekly summaries directory and outputs the save path for this week's summary
# Usage: bash scripts/weekly-save-path.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
SAVE_DIR=$(yq '.reporting.week.save_dir // "weekly"' "$CONFIG")
YEAR=$(date +%Y)
WEEK_NUM=$(date +%V)

mkdir -p "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}"
echo "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}/${YEAR}-W${WEEK_NUM}-week.md"
