#!/usr/bin/env bash
set -euo pipefail

# Reads tracker.yaml and outputs application pipeline status
# Used by day-start, check-in, and day-end commands

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
OUTPUT_DIR=""
if command -v yq &>/dev/null && [[ -f "$CONFIG" ]]; then
  OUTPUT_DIR=$(yq '.output_dir' "$CONFIG")
  OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"
fi

echo "=== Application Pipeline ==="
bash "${SCRIPT_DIR}/tracker.sh" stats

echo ""
echo "=== Follow-ups Due ==="
bash "${SCRIPT_DIR}/tracker.sh" follow-ups

echo ""
echo "=== Active Applications ==="
bash "${SCRIPT_DIR}/tracker.sh" list

if [[ -n "$OUTPUT_DIR" ]]; then
  QUEUE_FILE="${OUTPUT_DIR}/job-queue-$(date +%Y-%m-%d).md"
  if [[ -f "$QUEUE_FILE" ]]; then
    search_count=$(grep -c "^- \[" "$QUEUE_FILE" 2>/dev/null || echo 0)
    echo ""
    echo "=== Job Search Queue ($(date +%Y-%m-%d)) ==="
    echo "${search_count} searches ready | Queue: ${QUEUE_FILE}"
  fi
fi
