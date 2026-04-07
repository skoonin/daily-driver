#!/usr/bin/env bash
set -euo pipefail

# Reads tracker.yaml and outputs application pipeline status
# Used by day-start, check-in, and day-end commands

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Application Pipeline ==="
bash "${SCRIPT_DIR}/tracker.sh" stats

echo ""
echo "=== Follow-ups Due ==="
bash "${SCRIPT_DIR}/tracker.sh" follow-ups

echo ""
echo "=== Active Applications ==="
bash "${SCRIPT_DIR}/tracker.sh" list
