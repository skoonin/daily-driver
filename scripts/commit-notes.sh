#!/usr/bin/env bash
set -euo pipefail

# Commits output-dir notes with a label determined by type argument.
# Usage: bash scripts/commit-notes.sh {daily|weekly|monthly}

TYPE="${1:-}"
case "$TYPE" in
  daily)   LABEL="daily notes: $(date +%Y-%m-%d)" ;;
  weekly)  LABEL="weekly summary: $(date +%Y)-W$(date +%V)" ;;
  monthly) LABEL="monthly summary: $(date +%Y)-$(date +%m) $(date +%B)" ;;
  *) echo "Usage: $0 {daily|weekly|monthly}" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

if ! git -C "$OUTPUT_DIR" add -A; then
  echo "ERROR: git add failed in ${OUTPUT_DIR}" >&2
  exit 1
fi
git -C "$OUTPUT_DIR" commit -m "$LABEL" || {
  if git -C "$OUTPUT_DIR" diff --cached --quiet 2>/dev/null; then
    echo "(nothing to commit)"
  else
    echo "ERROR: git commit failed in ${OUTPUT_DIR}" >&2
    exit 1
  fi
}
