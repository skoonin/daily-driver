#!/usr/bin/env bash
set -euo pipefail

# Lists output directory contents or reports not found
# Usage: bash scripts/check-output-dir.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

ls -la "$OUTPUT_DIR/" 2>/dev/null || echo "output dir not found: ${OUTPUT_DIR}"
