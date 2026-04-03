#!/usr/bin/env bash
set -euo pipefail

# Resolves output_dir from config.yaml, expanding leading ~ to $HOME
# Usage: OUTPUT_DIR=$(bash scripts/get-output-dir.sh)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

RAW=$(yq '.output_dir' "$CONFIG")
if [[ -z "$RAW" || "$RAW" == "null" ]]; then
  echo "ERROR: output_dir is not set in ${CONFIG}" >&2
  exit 1
fi
echo "${RAW/#\~/$HOME}"
