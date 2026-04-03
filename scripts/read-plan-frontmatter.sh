#!/usr/bin/env bash
set -euo pipefail

# Prints today's plan file YAML frontmatter (content between first two --- delimiters)
# Usage: bash scripts/read-plan-frontmatter.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
TODAY=$(date +%Y-%m-%d)
YEAR=$(date +%Y)
MONTH=$(date +%m)
PLAN="${OUTPUT_DIR}/${YEAR}/${MONTH}/${TODAY}-plan.md"

if [[ -f "$PLAN" ]]; then
  awk '/^---$/{c++;next}c==1' "$PLAN"
else
  echo "(no plan found for today -- run /day-start first)"
fi
