#!/usr/bin/env bash
set -euo pipefail

# Copies tracker.yaml into state_dir as a timestamped snapshot before a
# session modifies anything. build-session-delta.sh diffs the most recent
# snapshot against the live tracker to report what changed.
#
# Session ID priority:
#   1. $1 (explicit arg)
#   2. $CLAUDE_SESSION_ID (set by Claude Code at session start)
#   3. date +%Y-%m-%d-%H%M%S fallback
#
# Usage: bash scripts/snapshot-tracker.sh [session-id]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi

TRACKER="${OUTPUT_DIR}/tracker.yaml"

# No-op when there is nothing to snapshot yet
if [[ ! -f "$TRACKER" ]]; then
  echo "INFO: $(basename "$0"): tracker.yaml not found at ${TRACKER} -- skipping snapshot" >&2
  exit 0
fi

SESSION_ID="${1:-${CLAUDE_SESSION_ID:-$(date +%Y-%m-%d-%H%M%S)}}"

mkdir -p "$STATE_DIR"
DEST="${STATE_DIR}/tracker-snapshot-${SESSION_ID}.yaml"

cp "$TRACKER" "$DEST"
echo "Tracker snapshot saved: ${DEST}"
