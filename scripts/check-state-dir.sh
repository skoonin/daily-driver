#!/usr/bin/env bash
set -euo pipefail

# Reports whether the runtime state directory exists
# Usage: bash scripts/check-state-dir.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "State dir: ERROR resolving state_dir from config: ${STATE_DIR}"
  exit 1
fi
if [[ -d "$STATE_DIR" ]]; then
  echo "State dir: OK (${STATE_DIR})"
else
  echo "State dir: will be created on first /check-in"
fi
