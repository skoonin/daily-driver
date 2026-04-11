#!/usr/bin/env bash
set -euo pipefail

# Outputs standup save flag and resolved file path
# Usage: bash scripts/standup-save-path.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi
TODAY=$(date +%Y-%m-%d)
YEAR=$(date +%Y)
MONTH=$(date +%m)

if ! SAVE=$(yq '.reporting.standup.save // false' "$CONFIG" 2>&1); then
  echo "ERROR: standup-save-path: could not read standup.save from config: ${SAVE}" >&2
  exit 1
fi
echo "save=${SAVE} path=${OUTPUT_DIR}/${YEAR}/${MONTH}/${TODAY}-standup.md"
