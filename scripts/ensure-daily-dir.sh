#!/usr/bin/env bash
set -euo pipefail

# Creates today's output directory and prints the plan file path
# Usage: bash scripts/ensure-daily-dir.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi
TODAY=$(date +%Y-%m-%d)
YEAR=$(date +%Y)
MONTH=$(date +%m)
TARGET="${OUTPUT_DIR}/${YEAR}/${MONTH}"

mkdir -p "$TARGET"
echo "${TARGET}/${TODAY}-plan.md"
