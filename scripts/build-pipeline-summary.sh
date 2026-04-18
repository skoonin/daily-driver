#!/usr/bin/env bash
set -euo pipefail

# Builds {state_dir}/pipeline-summary.yaml from tracker.yaml and jobs.csv.
# Called at day-end and after gather-jobs to give day-start/check-in a
# pre-computed snapshot instead of re-querying files each session.
#
# Usage: bash scripts/build-pipeline-summary.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

# --- prerequisites ------------------------------------------------------------

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq not installed" >&2
  exit 1
fi
if ! command -v jq &>/dev/null; then
  echo "ERROR: jq not installed" >&2
  exit 1
fi

# --- resolve paths ------------------------------------------------------------

if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: build-pipeline-summary: could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

if ! STATE_DIR=$(bash "${SCRIPT_DIR}/get-state-dir.sh" 2>&1); then
  echo "ERROR: build-pipeline-summary: could not resolve state_dir: ${STATE_DIR}" >&2
  exit 1
fi

TRACKER="${OUTPUT_DIR}/tracker.yaml"
JOBS_CSV="${OUTPUT_DIR}/jobs.csv"
LAST_RUN_JSON="${OUTPUT_DIR}/jobs-last-run.json"
RULED_OUT_YAML="${STATE_DIR}/ruled-out.yaml"
SUMMARY_OUT="${STATE_DIR}/pipeline-summary.yaml"
TODAY=$(date +%Y-%m-%d)
GENERATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

mkdir -p "$STATE_DIR"

# --- stale threshold (days) from config, default 14 --------------------------

if ! STALE_DAYS=$(yq '.tracker.stale_threshold_days // 14' "$CONFIG" 2>/dev/null); then
  STALE_DAYS=14
fi
if [[ -z "$STALE_DAYS" || "$STALE_DAYS" == "null" || ! "$STALE_DAYS" =~ ^[0-9]+$ ]]; then
  STALE_DAYS=14
fi

# --- top-N threshold from config, default 10 ---------------------------------

TOP_N=10

# --- tracker section ----------------------------------------------------------

tracker_counts() {
  if [[ ! -f "$TRACKER" ]]; then
    echo "    applied: 0"
    echo "    screening: 0"
    echo "    interviewing: 0"
    echo "    offer: 0"
    echo "    rejected: 0"
    echo "    ghosted: 0"
    echo "    dropped: 0"
    echo "    withdrawn: 0"
    return
  fi

  local apps_json
  if ! apps_json=$(yq -o=json '.applications // []' "$TRACKER" 2>/dev/null); then
    echo "WARNING: build-pipeline-summary: could not read tracker.yaml" >&2
    echo "    applied: 0"
    return
  fi

  echo "$apps_json" | jq -r '[
    "    applied: \([.[] | select(.status == "applied")] | length)",
    "    screening: \([.[] | select(.status == "screening")] | length)",
    "    interviewing: \([.[] | select(.status == "interviewing")] | length)",
    "    offer: \([.[] | select(.status == "offer")] | length)",
    "    rejected: \([.[] | select(.status == "rejected")] | length)",
    "    ghosted: \([.[] | select(.status == "ghosted")] | length)",
    "    dropped: \([.[] | select(.status == "dropped")] | length)",
    "    withdrawn: \([.[] | select(.status == "withdrawn")] | length)"
  ] | .[]'
}

follow_ups_due() {
  if [[ ! -f "$TRACKER" ]]; then
    echo "    count: 0"
    echo "    items: []"
    return
  fi

  local apps_json
  if ! apps_json=$(yq -o=json '.applications // []' "$TRACKER" 2>/dev/null); then
    echo "    count: 0"
    echo "    items: []"
    return
  fi

  # Collect follow-up items where follow_up_date <= today and status is active
  local due_json
  due_json=$(echo "$apps_json" | jq -r --arg today "$TODAY" '[
    .[] | select(
      (.status == "applied" or .status == "screening" or .status == "interviewing") and
      (.follow_up_date // "") != "" and
      (.follow_up_date <= $today)
    ) | {id: .id, company: .company, role: .role, follow_up_date: .follow_up_date}
  ]')

  local count
  count=$(echo "$due_json" | jq 'length')
  echo "    count: ${count}"
  if [[ "$count" -gt 0 ]]; then
    echo "    items:"
    echo "$due_json" | jq -r '.[] | "      - id: \(.id)\n        company: \(.company)\n        role: \(.role)\n        follow_up_date: \(.follow_up_date)"'
  else
    echo "    items: []"
  fi
}

stale_apps() {
  if [[ ! -f "$TRACKER" ]]; then
    echo "    count: 0"
    echo "    items: []"
    return
  fi

  local apps_json
  if ! apps_json=$(yq -o=json '.applications // []' "$TRACKER" 2>/dev/null); then
    echo "    count: 0"
    echo "    items: []"
    return
  fi

  local today_epoch
  if ! today_epoch=$(date -j -f "%Y-%m-%d" "$TODAY" +%s 2>/dev/null); then
    echo "WARNING: build-pipeline-summary: could not compute today epoch for stale check" >&2
    echo "    count: 0"
    echo "    items: []"
    return
  fi

  local stale_items=""
  local count=0

  while IFS=$'\t' read -r id company role status last_activity; do
    [[ -z "$id" ]] && continue
    # Only active statuses are worth flagging as stale
    if [[ "$status" != "applied" && "$status" != "screening" && "$status" != "interviewing" ]]; then
      continue
    fi
    local la_epoch
    if ! la_epoch=$(date -j -f "%Y-%m-%d" "$last_activity" +%s 2>/dev/null); then
      continue
    fi
    local days_since=$(( (today_epoch - la_epoch) / 86400 ))
    if [[ "$days_since" -ge "$STALE_DAYS" ]]; then
      stale_items+=$(printf "      - id: %s\n        company: %s\n        role: %s\n        days_since_activity: %d\n" \
        "$id" "$company" "$role" "$days_since")
      count=$((count + 1))
    fi
  done < <(echo "$apps_json" | jq -r '.[] | [.id, .company, .role, .status, .last_activity] | @tsv')

  echo "    count: ${count}"
  if [[ "$count" -gt 0 ]]; then
    echo "    items:"
    printf '%s' "$stale_items"
  else
    echo "    items: []"
  fi
}

# --- jobs.csv section ---------------------------------------------------------

# Parse jobs.csv using Python csv module (handles embedded commas/quotes).
# Outputs TSV: Status\tCompany\tRole\tFit\tDateFound
parse_jobs_csv() {
  if [[ ! -f "$JOBS_CSV" ]]; then
    return 0
  fi
  python3 - "$JOBS_CSV" <<'PYEOF'
import csv, sys
with open(sys.argv[1], newline='', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        print('\t'.join([
            row.get('Status', '').strip(),
            row.get('Company', '').strip(),
            row.get('Role', '').strip(),
            row.get('Fit', '').strip(),
            row.get('Date Found', '').strip(),
        ]))
PYEOF
}

jobs_csv_section() {
  if [[ ! -f "$JOBS_CSV" ]]; then
    cat <<'YAML'
  counts:
    found: 0
    pending: 0
    applied: 0
    skipped: 0
    rejected: 0
    other: 0
  new_since_last_scrape: 0
  top_prospects: []
YAML
    return
  fi

  local csv_tsv
  if ! csv_tsv=$(parse_jobs_csv); then
    echo "WARNING: build-pipeline-summary: could not parse jobs.csv" >&2
    cat <<'YAML'
  counts:
    found: 0
    pending: 0
    applied: 0
    skipped: 0
    rejected: 0
    other: 0
  new_since_last_scrape: 0
  top_prospects: []
YAML
    return
  fi

  # Count by status
  local found=0 pending=0 applied=0 skipped=0 rejected=0 other=0
  local -a top_rows=()

  while IFS=$'\t' read -r status company role fit date_found; do
    [[ -z "$status" ]] && continue

    case "$status" in
      found)       found=$((found + 1)) ;;
      pending)     pending=$((pending + 1)) ;;
      applied)     applied=$((applied + 1)) ;;
      skipped)     skipped=$((skipped + 1)) ;;
      rejected|ghosted|dropped|withdrawn|interviewing|screening|offer) other=$((other + 1)) ;;
      *)           other=$((other + 1)) ;;
    esac

    # Top prospects: found or pending with numeric Fit >= 8
    if [[ "$status" == "found" || "$status" == "pending" ]]; then
      if [[ -n "$fit" ]] && [[ "$fit" =~ ^[0-9]+(/[0-9]+)?$ ]]; then
        local fit_num="${fit%%/*}"  # strip "/10" suffix if present
        if [[ "$fit_num" =~ ^[0-9]+$ ]] && [[ "$fit_num" -ge 8 ]]; then
          top_rows+=("${fit_num}	${company}	${role}	${status}	${date_found}")
        fi
      fi
    fi
  done <<< "$csv_tsv"

  # How many rows were added since the previous pipeline-summary snapshot
  local prev_total=0
  if [[ -f "$SUMMARY_OUT" ]]; then
    if ! prev_total=$(yq '.jobs_csv.counts.total // 0' "$SUMMARY_OUT" 2>/dev/null); then
      prev_total=0
    fi
    if [[ ! "$prev_total" =~ ^[0-9]+$ ]]; then
      prev_total=0
    fi
  fi
  local current_total=$(( found + pending + applied + skipped + other ))
  local new_since=$(( current_total > prev_total ? current_total - prev_total : 0 ))

  echo "  counts:"
  echo "    found: ${found}"
  echo "    pending: ${pending}"
  echo "    applied: ${applied}"
  echo "    skipped: ${skipped}"
  echo "    rejected_or_closed: ${other}"
  echo "    total: ${current_total}"
  echo "  new_since_last_scrape: ${new_since}"

  # Sort top rows by fit descending and cap at TOP_N
  if [[ "${#top_rows[@]}" -gt 0 ]]; then
    echo "  top_prospects:"
    local count=0
    while IFS=$'\t' read -r fit_num company role status date_found; do
      [[ "$count" -ge "$TOP_N" ]] && break
      printf "    - company: %s\n      role: %s\n      fit: %s\n      status: %s\n      date_found: %s\n" \
        "$company" "$role" "$fit_num" "$status" "$date_found"
      count=$((count + 1))
    done < <(printf '%s\n' "${top_rows[@]}" | sort -t$'\t' -k1 -rn)
  else
    echo "  top_prospects: []"
  fi
}

# --- last scrape timestamp ----------------------------------------------------

last_scrape_ts() {
  if [[ -f "$LAST_RUN_JSON" ]]; then
    local ts
    if ts=$(jq -r '.finished_at // .started_at // empty' "$LAST_RUN_JSON" 2>/dev/null) && [[ -n "$ts" ]]; then
      echo "$ts"
      return
    fi
  fi
  echo "unknown"
}

# --- assemble YAML ------------------------------------------------------------

{
  echo "# Pipeline summary -- auto-generated by build-pipeline-summary.sh"
  echo "# Do not edit manually; it is overwritten at day-end and after gather-jobs."
  echo "generated_at: ${GENERATED_AT}"
  echo ""
  echo "tracker:"
  echo "  counts_by_status:"
  tracker_counts
  echo "  follow_ups_due:"
  follow_ups_due
  echo "  stale_apps:"
  stale_apps
  echo ""
  echo "jobs_csv:"
  jobs_csv_section
  echo ""
  echo "last_scrape: \"$(last_scrape_ts)\""
} > "${SUMMARY_OUT}.tmp"

# Filter ruled-out jobs from top_prospects in the assembled summary.
# Matches on company|role prefix of each job_key (link not available at this point).
if [[ -f "$RULED_OUT_YAML" ]]; then
  ruled_count=""
  if ruled_count=$(yq '.ruled_out | length' "$RULED_OUT_YAML" 2>/dev/null) && [[ "$ruled_count" =~ ^[0-9]+$ ]] && [[ "$ruled_count" -gt 0 ]]; then
    # Build a newline-separated list of "company|role" prefixes from ruled-out job_keys
    RULED_PAIRS=$(yq -r '.ruled_out[].job_key' "$RULED_OUT_YAML" 2>/dev/null \
      | awk -F'|' '{print $1 "|" $2}' | sort -u)

    if [[ -n "$RULED_PAIRS" ]]; then
      # Remove top_prospects blocks whose company+role match a ruled-out pair.
      # The YAML block for each prospect spans lines: "    - company: X\n      role: Y\n..."
      # Use Python for reliable multi-line block removal.
      python3 - "${SUMMARY_OUT}.tmp" "$RULED_PAIRS" <<'PYEOF'
import sys, re

path = sys.argv[1]
ruled_pairs_raw = sys.argv[2]  # newline-separated "company|role" strings
ruled = set(ruled_pairs_raw.strip().splitlines())

with open(path) as f:
    content = f.read()

# Each prospect block starts with "    - company:" and ends before the next "    - company:" or a line
# that is not indented by 6+ spaces (i.e. back to the parent level).
def filter_prospects(content, ruled):
    lines = content.splitlines(keepends=True)
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Detect start of a prospect block
        if re.match(r'    - company: (.+)', line):
            company = re.match(r'    - company: (.+)', line).group(1).strip()
            block = [line]
            j = i + 1
            while j < len(lines) and not re.match(r'    - ', lines[j]) and re.match(r'      ', lines[j]):
                block.append(lines[j])
                j += 1
            # Extract role from block
            role = ''
            for bl in block:
                m = re.match(r'      role: (.+)', bl)
                if m:
                    role = m.group(1).strip()
                    break
            pair = f'{company}|{role}'
            if pair not in ruled:
                result.extend(block)
            i = j
        else:
            result.append(line)
            i += 1
    return ''.join(result)

filtered = filter_prospects(content, ruled)
with open(path, 'w') as f:
    f.write(filtered)
PYEOF
    fi
    echo "# ruled_out_filtered: ${ruled_count} entries applied" >> "${SUMMARY_OUT}.tmp"
  fi
fi

mv "${SUMMARY_OUT}.tmp" "$SUMMARY_OUT"
echo "Pipeline summary written: ${SUMMARY_OUT}"
