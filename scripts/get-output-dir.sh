#!/usr/bin/env bash
set -euo pipefail

# Resolves output_dir from config.yaml, expanding leading ~ to $HOME
# Usage: OUTPUT_DIR=$(bash scripts/get-output-dir.sh)

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

if ! RAW=$(yq '.output_dir' "$CONFIG" 2>&1); then
  echo "ERROR: get-output-dir: yq failed reading output_dir: ${RAW}" >&2
  exit 1
fi
if [[ -z "$RAW" || "$RAW" == "null" ]]; then
  echo "ERROR: output_dir is not set in ${CONFIG}" >&2
  exit 1
fi
echo "${RAW/#\~/$HOME}"
