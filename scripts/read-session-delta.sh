#!/usr/bin/env bash
set -euo pipefail

# Reads {state_dir}/session-delta.yaml and pretty-prints a one-line summary
# of what changed since the last session.
#
# Usage: bash scripts/read-session-delta.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi

DELTA="${STATE_DIR}/session-delta.yaml"

if [[ ! -f "$DELTA" ]]; then
  echo "No session delta yet at ${DELTA}."
  echo "  Run 'bash scripts/build-session-delta.sh' at day-end to populate it."
  exit 0
fi

if ! command -v yq &>/dev/null; then
  echo "ERROR: $(basename "$0"): yq not installed" >&2
  exit 1
fi

since=$(yq '.since // "unknown"' "$DELTA" 2>/dev/null || echo "unknown")

new_count=$(yq '.tracker_changes.new | length // 0' "$DELTA" 2>/dev/null || echo "0")
[[ "$new_count" =~ ^[0-9]+$ ]] || new_count=0

status_count=$(yq '.tracker_changes.status_changed | length // 0' "$DELTA" 2>/dev/null || echo "0")
[[ "$status_count" =~ ^[0-9]+$ ]] || status_count=0

new_jobs=$(yq '.new_jobs // 0' "$DELTA" 2>/dev/null || echo "0")
[[ "$new_jobs" =~ ^[0-9]+$ ]] || new_jobs=0

follow_ups=$(yq '.follow_ups_done // 0' "$DELTA" 2>/dev/null || echo "0")
[[ "$follow_ups" =~ ^[0-9]+$ ]] || follow_ups=0

ruled_out=$(yq '.ruled_out_today // 0' "$DELTA" 2>/dev/null || echo "0")
[[ "$ruled_out" =~ ^[0-9]+$ ]] || ruled_out=0

echo "=== Session Delta ==="
if [[ "$since" == "null" || "$since" == "unknown" ]]; then
  echo "Since: first session (no prior snapshot)"
else
  # Convert ISO-8601 to a more readable local time for display
  since_display=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$since" +"%Y-%m-%d %H:%M" 2>/dev/null || echo "$since")
  echo "Since: ${since_display}"
fi
echo ""
echo "Since last session: ${new_count} new app(s), ${status_count} status change(s), ${new_jobs} new job(s) found, ${follow_ups} follow-up(s) done"

# ── Detail blocks (only shown when there is something to show) ────────────────

if [[ "$new_count" -gt 0 ]]; then
  echo ""
  echo "New applications:"
  yq '.tracker_changes.new[]' "$DELTA" 2>/dev/null | while read -r app_id; do
    echo "  + ${app_id}"
  done
fi

if [[ "$status_count" -gt 0 ]]; then
  echo ""
  echo "Status changes:"
  yq -o=json '.tracker_changes.status_changed[]' "$DELTA" 2>/dev/null \
    | jq -r '"  " + .app_id + ": " + .from + " -> " + .to' 2>/dev/null || true
fi

if [[ "$ruled_out" -gt 0 ]]; then
  echo ""
  echo "Ruled out today: ${ruled_out} role(s) (see ${STATE_DIR}/ruled-out.yaml)"
fi
