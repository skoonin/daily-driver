#!/usr/bin/env bash
set -euo pipefail

# Creates the monthly summaries directory and outputs the save path for this month's summary
# Usage: bash scripts/monthly-save-path.sh [YEAR] [MONTH] [MONTH_NAME]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi
if ! SAVE_DIR=$(yq '.reporting.month.save_dir // "monthly"' "$CONFIG" 2>&1); then
  echo "ERROR: monthly-save-path: could not read save_dir from config: ${SAVE_DIR}" >&2
  exit 1
fi

YEAR="${1:-$(date +%Y)}"
MONTH="${2:-$(date +%m)}"
MONTH_NAME="${3:-$(date +%B)}"

if [[ ! "$YEAR" =~ ^[0-9]{4}$ ]]; then
  echo "ERROR: YEAR must be 4 digits (got: ${YEAR})" >&2; exit 1
fi
if [[ ! "$MONTH" =~ ^[0-9]{2}$ ]]; then
  echo "ERROR: MONTH must be 2 digits (got: ${MONTH})" >&2; exit 1
fi
if [[ ! "$MONTH_NAME" =~ ^[A-Za-z]+$ ]]; then
  echo "ERROR: MONTH_NAME must be alphabetic (got: ${MONTH_NAME})" >&2; exit 1
fi

mkdir -p "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}"
echo "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}/${YEAR}-${MONTH}-${MONTH_NAME}-month.md"
