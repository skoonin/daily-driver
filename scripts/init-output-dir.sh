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

REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ ! -f "${OUTPUT_DIR}/context.md" && -f "${REPO_DIR}/context.md.example" ]]; then
  cp "${REPO_DIR}/context.md.example" "${OUTPUT_DIR}/context.md"
  echo "Copied context.md.example to ${OUTPUT_DIR}/context.md -- edit it with your details"
fi

echo "Initialized: ${OUTPUT_DIR}"
