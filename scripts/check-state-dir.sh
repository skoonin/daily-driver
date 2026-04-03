#!/usr/bin/env bash
set -euo pipefail

# Reports whether the runtime state directory exists
# Usage: bash scripts/check-state-dir.sh

STATE_DIR="${HOME}/.local/share/daily-driver"
if [[ -d "$STATE_DIR" ]]; then
  echo "State dir: OK (${STATE_DIR})"
else
  echo "State dir: will be created on first /check-in"
fi
