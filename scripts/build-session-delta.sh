#!/usr/bin/env bash
set -euo pipefail

# Diffs the most recent tracker snapshot against the current tracker.yaml,
# then assembles session-delta.yaml for the next session's read-session-delta.sh.
#
# Fields written to {state_dir}/session-delta.yaml:
#   generated_at    ISO-8601 timestamp
#   since           snapshot mtime in ISO-8601 (null when no prior snapshot)
#   tracker_changes.new             [app_id, ...]
#   tracker_changes.status_changed  [{app_id, from, to}, ...]
#   new_jobs        count of jobs.csv rows with Date Found >= snapshot mtime
#   ruled_out_today count of ruled-out.yaml entries (0 if file absent)
#   follow_ups_done count of tracker entries whose follow_up_date was set since snapshot
#
# Usage: bash scripts/build-session-delta.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: $(basename "$0"): could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi

mkdir -p "$STATE_DIR"

TRACKER="${OUTPUT_DIR}/tracker.yaml"
JOBS_CSV="${OUTPUT_DIR}/jobs.csv"
RULED_OUT="${STATE_DIR}/ruled-out.yaml"
OUT="${STATE_DIR}/session-delta.yaml"
GENERATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── Find most recent snapshot ─────────────────────────────────────────────────

SNAPSHOT=""
if compgen -G "${STATE_DIR}/tracker-snapshot-*.yaml" &>/dev/null; then
  SNAPSHOT=$(ls -t "${STATE_DIR}"/tracker-snapshot-*.yaml 2>/dev/null | head -1)
fi

# ── No snapshot: first session or state wiped ────────────────────────────────

if [[ -z "$SNAPSHOT" ]]; then
  cat > "$OUT" <<YAML
generated_at: "${GENERATED_AT}"
since: null
tracker_changes:
  new: []
  status_changed: []
new_jobs: 0
ruled_out_today: 0
follow_ups_done: 0
YAML
  echo "session-delta.yaml written (no prior snapshot -- first session)" >&2
  echo "Session delta saved: ${OUT}"
  exit 0
fi

# ── Resolve snapshot timestamp ────────────────────────────────────────────────

# macOS stat reports mtime seconds via -f %m; convert to ISO-8601
SNAPSHOT_MTIME_SEC=$(stat -f "%m" "$SNAPSHOT" 2>/dev/null || echo "0")
SINCE=$(date -r "$SNAPSHOT_MTIME_SEC" -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "null")

# ── Guard: current tracker may not exist ─────────────────────────────────────

if [[ ! -f "$TRACKER" ]]; then
  cat > "$OUT" <<YAML
generated_at: "${GENERATED_AT}"
since: "${SINCE}"
tracker_changes:
  new: []
  status_changed: []
new_jobs: 0
ruled_out_today: 0
follow_ups_done: 0
YAML
  echo "session-delta.yaml written (no current tracker.yaml)" >&2
  echo "Session delta saved: ${OUT}"
  exit 0
fi

if ! command -v yq &>/dev/null; then
  echo "ERROR: $(basename "$0"): yq not installed" >&2
  exit 1
fi

# ── Diff tracker: new and status-changed applications ────────────────────────

# Extract {app_id: status} maps from both files as TSV
snap_tsv=$(yq -o=json '.applications // {}' "$SNAPSHOT" 2>/dev/null \
  | jq -r 'to_entries[] | [.key, (.value.status // "unknown")] | @tsv' 2>/dev/null || true)

curr_tsv=$(yq -o=json '.applications // {}' "$TRACKER" 2>/dev/null \
  | jq -r 'to_entries[] | [.key, (.value.status // "unknown")] | @tsv' 2>/dev/null || true)

# Build associative arrays from snapshot and current
declare -A snap_status
while IFS=$'\t' read -r app_id status; do
  [[ -z "$app_id" ]] && continue
  snap_status["$app_id"]="$status"
done <<< "$snap_tsv"

declare -A curr_status
while IFS=$'\t' read -r app_id status; do
  [[ -z "$app_id" ]] && continue
  curr_status["$app_id"]="$status"
done <<< "$curr_tsv"

new_apps=()
status_changes=()

for app_id in "${!curr_status[@]}"; do
  if [[ -z "${snap_status[$app_id]:-}" ]]; then
    new_apps+=("$app_id")
  elif [[ "${snap_status[$app_id]}" != "${curr_status[$app_id]}" ]]; then
    status_changes+=("${app_id}|${snap_status[$app_id]}|${curr_status[$app_id]}")
  fi
done

# ── Count new jobs.csv rows since snapshot ────────────────────────────────────

new_jobs=0
if [[ -f "$JOBS_CSV" ]] && command -v python3 &>/dev/null; then
  new_jobs=$(python3 - "$JOBS_CSV" "$SNAPSHOT_MTIME_SEC" <<'PYEOF'
import sys, csv, datetime, os

csv_path = sys.argv[1]
since_epoch = int(sys.argv[2])
since_dt = datetime.datetime.utcfromtimestamp(since_epoch).date()

count = 0
try:
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        date_col = next((c for c in (reader.fieldnames or [])
                         if 'date found' in c.lower()), None)
        if not date_col:
            sys.exit(0)
        for row in reader:
            raw = row.get(date_col, '').strip()
            if not raw:
                continue
            try:
                found_dt = datetime.date.fromisoformat(raw)
                if found_dt >= since_dt:
                    count += 1
            except ValueError:
                pass
except Exception:
    pass
print(count)
PYEOF
  ) || new_jobs=0
fi

# ── Count ruled-out entries ───────────────────────────────────────────────────

ruled_out_today=0
if [[ -f "$RULED_OUT" ]]; then
  if ! ruled_out_today=$(yq '.ruled_out | length // 0' "$RULED_OUT" 2>/dev/null); then
    ruled_out_today=0
  fi
  [[ "$ruled_out_today" =~ ^[0-9]+$ ]] || ruled_out_today=0
fi

# ── Count follow-ups completed since snapshot ─────────────────────────────────
# A follow_up_date being set (non-null) in current but absent in snapshot
# indicates it was recorded during this session window.

follow_ups_done=0
if [[ -n "$snap_tsv" && -n "$curr_tsv" ]]; then
  snap_fu_tsv=$(yq -o=json '.applications // {}' "$SNAPSHOT" 2>/dev/null \
    | jq -r 'to_entries[] | [.key, (.value.follow_up_date // "")] | @tsv' 2>/dev/null || true)
  curr_fu_tsv=$(yq -o=json '.applications // {}' "$TRACKER" 2>/dev/null \
    | jq -r 'to_entries[] | [.key, (.value.follow_up_date // "")] | @tsv' 2>/dev/null || true)

  declare -A snap_fu
  while IFS=$'\t' read -r app_id fu_date; do
    [[ -z "$app_id" ]] && continue
    snap_fu["$app_id"]="$fu_date"
  done <<< "$snap_fu_tsv"

  while IFS=$'\t' read -r app_id fu_date; do
    [[ -z "$app_id" ]] && continue
    [[ -z "$fu_date" ]] && continue
    snap_val="${snap_fu[$app_id]:-}"
    # Count entries where follow_up_date was absent before and is now set
    if [[ -z "$snap_val" && -n "$fu_date" ]]; then
      follow_ups_done=$((follow_ups_done + 1))
    fi
  done <<< "$curr_fu_tsv"
fi

# ── Build YAML output ─────────────────────────────────────────────────────────

{
  echo "generated_at: \"${GENERATED_AT}\""
  echo "since: \"${SINCE}\""
  echo "tracker_changes:"
  echo "  new:"
  for app_id in "${new_apps[@]:-}"; do
    [[ -z "$app_id" ]] && continue
    echo "    - ${app_id}"
  done
  echo "  status_changed:"
  for entry in "${status_changes[@]:-}"; do
    [[ -z "$entry" ]] && continue
    IFS='|' read -r app_id from_s to_s <<< "$entry"
    echo "    - app_id: ${app_id}"
    echo "      from: ${from_s}"
    echo "      to: ${to_s}"
  done
  echo "new_jobs: ${new_jobs}"
  echo "ruled_out_today: ${ruled_out_today}"
  echo "follow_ups_done: ${follow_ups_done}"
} > "$OUT"

# Fix the empty-list YAML: if the block only has the header and nothing under
# it, rewrite to inline empty list form that yq/jq can parse unambiguously.
python3 - "$OUT" <<'PYEOF'
import re, sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()

# "  new:\n  status_changed:" -> "  new: []\n  status_changed:"
content = re.sub(r'(  new:)\n(  status_changed:)', r'\1 []\n\2', content)
# "  status_changed:\nnew_jobs:" -> "  status_changed: []\nnew_jobs:"
content = re.sub(r'(  status_changed:)\n(new_jobs:)', r'\1 []\n\2', content)

with open(path, 'w') as f:
    f.write(content)
PYEOF

echo "Session delta saved: ${OUT}"
