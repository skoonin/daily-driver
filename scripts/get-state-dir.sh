#!/usr/bin/env bash
set -euo pipefail

# Resolves state_dir from config.yaml, expanding leading ~ to $HOME.
# Falls back to ~/.local/share/daily-driver if key is absent.
# Usage: STATE_DIR=$(bash scripts/get-state-dir.sh)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq not installed" >&2
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: config.yaml not found at ${CONFIG}" >&2
  exit 1
fi

if ! RAW=$(yq '.state_dir // "~/.local/share/daily-driver"' "$CONFIG" 2>&1); then
  echo "ERROR: get-state-dir: yq failed reading state_dir: ${RAW}" >&2
  exit 1
fi
echo "${RAW/#\~/$HOME}"
