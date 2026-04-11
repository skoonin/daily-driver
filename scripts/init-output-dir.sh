#!/usr/bin/env bash
set -euo pipefail

# Creates output directory and initializes as git repo if not already one
# Usage: bash scripts/init-output-dir.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
if ! git -C "$OUTPUT_DIR" init 2>&1; then
  echo "ERROR: git init failed in ${OUTPUT_DIR}" >&2
  exit 1
fi
echo "Initialized: ${OUTPUT_DIR}"
