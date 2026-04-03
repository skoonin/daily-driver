#!/usr/bin/env bash
set -euo pipefail

# Creates output directory and initializes as git repo if not already one
# Usage: bash scripts/init-output-dir.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")

mkdir -p "$OUTPUT_DIR" && git -C "$OUTPUT_DIR" init 2>/dev/null && echo "Initialized: ${OUTPUT_DIR}"
