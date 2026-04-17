#!/usr/bin/env bash
set -euo pipefail

# Reads voice-profile.md from the configured output_dir.
# Non-fatal: if the file doesn't exist, warns to stderr and exits 0.
# Usage: bash scripts/read-voice-profile.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

PROFILE="${OUTPUT_DIR}/voice-profile.md"
if [[ ! -f "$PROFILE" ]]; then
  echo "WARNING: voice-profile.md not found at ${PROFILE} -- skipping voice profile load" >&2
  echo "         Run /voice-update with an approved writing sample to create it." >&2
  exit 0
fi

cat "$PROFILE"
