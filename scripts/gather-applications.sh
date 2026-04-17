#!/usr/bin/env bash
set -euo pipefail

# Reads tracker.yaml and outputs application pipeline status.
# Filters out entries whose company|role matches a ruled-out job_key prefix.
# Used by day-start, check-in, and day-end commands.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>/dev/null) || OUTPUT_DIR=""
STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>/dev/null) || STATE_DIR=""

echo "=== Application Pipeline ==="
bash "${SCRIPT_DIR}/tracker.sh" stats

echo ""
echo "=== Follow-ups Due ==="
bash "${SCRIPT_DIR}/tracker.sh" follow-ups

echo ""
echo "=== Active Applications ==="

RULED_OUT_YAML="${STATE_DIR}/ruled-out.yaml"

# When ruled-out.yaml exists, suppress any tracker entry whose company|role
# matches the company|role prefix of a recorded job_key.
if [[ -n "$STATE_DIR" && -f "$RULED_OUT_YAML" ]]; then
  RULED_PAIRS=$(yq -r '.ruled_out[].job_key' "$RULED_OUT_YAML" 2>/dev/null \
    | awk -F'|' '{print $1 "|" $2}' | sort -u || true)

  if [[ -n "$RULED_PAIRS" ]]; then
    bash "${SCRIPT_DIR}/tracker.sh" list | while IFS= read -r line; do
      # Lines describing an application contain "company - role" patterns;
      # suppress if "Company|Role" pair appears in the ruled-out set.
      skip=0
      while IFS= read -r pair; do
        company="${pair%%|*}"
        role="${pair##*|}"
        # Case-insensitive substring match against the output line
        if echo "$line" | grep -qiF "$company" && echo "$line" | grep -qiF "$role"; then
          skip=1
          break
        fi
      done <<< "$RULED_PAIRS"
      [[ "$skip" -eq 0 ]] && echo "$line"
    done
    echo "(ruled-out entries hidden; run 'bash scripts/list-ruled-out.sh' to review)"
  else
    bash "${SCRIPT_DIR}/tracker.sh" list
  fi
else
  bash "${SCRIPT_DIR}/tracker.sh" list
fi

if [[ -n "$OUTPUT_DIR" ]]; then
  QUEUE_FILE="${OUTPUT_DIR}/job-queue-$(date +%Y-%m-%d).md"
  if [[ -f "$QUEUE_FILE" ]]; then
    search_count=$(grep -c "^- \[" "$QUEUE_FILE" 2>/dev/null || echo 0)
    echo ""
    echo "=== Job Search Queue ($(date +%Y-%m-%d)) ==="
    echo "${search_count} searches ready | Queue: ${QUEUE_FILE}"
  fi
fi
