#!/usr/bin/env bash
set -euo pipefail

# Reads context.md from the configured output_dir.
# Usage: bash scripts/read-context.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

CONTEXT="${OUTPUT_DIR}/context.md"
if [[ ! -f "$CONTEXT" ]]; then
  echo "ERROR: context.md not found at ${CONTEXT}" >&2
  echo "       Copy context.md.example from the repo root to ${CONTEXT} and fill it in." >&2
  exit 1
fi

cat "$CONTEXT"
