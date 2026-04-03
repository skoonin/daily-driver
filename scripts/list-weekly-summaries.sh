#!/usr/bin/env bash
set -euo pipefail

# Lists weekly summary files for the given year (defaults to current year)
# Usage: bash scripts/list-weekly-summaries.sh [YEAR]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
SAVE_DIR=$(yq '.reporting.week.save_dir // "weekly"' "$CONFIG")
YEAR="${1:-$(date +%Y)}"

ls "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}/${YEAR}-W"*"-week.md" 2>/dev/null || echo "(no weekly summaries found for ${YEAR})"
