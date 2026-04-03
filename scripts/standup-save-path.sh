#!/usr/bin/env bash
set -euo pipefail

# Outputs standup save flag and resolved file path
# Usage: bash scripts/standup-save-path.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
TODAY=$(date +%Y-%m-%d)
YEAR=$(date +%Y)
MONTH=$(date +%m)

SAVE=$(yq '.reporting.standup.save // false' "$CONFIG")
echo "save=${SAVE} path=${OUTPUT_DIR}/${YEAR}/${MONTH}/${TODAY}-standup.md"
