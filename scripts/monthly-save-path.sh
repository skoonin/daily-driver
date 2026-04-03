#!/usr/bin/env bash
set -euo pipefail

# Creates the monthly summaries directory and outputs the save path for this month's summary
# Usage: bash scripts/monthly-save-path.sh [YEAR] [MONTH] [MONTH_NAME]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
SAVE_DIR=$(yq '.reporting.month.save_dir // "monthly"' "$CONFIG")

YEAR="${1:-$(date +%Y)}"
MONTH="${2:-$(date +%m)}"
MONTH_NAME="${3:-$(date +%B)}"

mkdir -p "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}"
echo "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}/${YEAR}-${MONTH}-${MONTH_NAME}-month.md"
