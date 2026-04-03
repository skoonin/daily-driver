#!/usr/bin/env bash
set -euo pipefail

# Commits today's plan and notes files in the output directory
# Usage: bash scripts/commit-daily-notes.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh")
TODAY=$(date +%Y-%m-%d)

git -C "$OUTPUT_DIR" add -A && git -C "$OUTPUT_DIR" commit -m "daily notes: ${TODAY}" 2>/dev/null || echo "(nothing to commit)"
