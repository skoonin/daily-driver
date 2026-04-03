#!/usr/bin/env bash
set -euo pipefail

# Prints today's full plan file
# Usage: bash scripts/read-plan.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
TODAY=$(date +%Y-%m-%d)
YEAR=$(date +%Y)
MONTH=$(date +%m)
PLAN="${OUTPUT_DIR}/${YEAR}/${MONTH}/${TODAY}-plan.md"

cat "$PLAN" 2>/dev/null || echo "(no plan found for today)"
