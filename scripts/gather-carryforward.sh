#!/usr/bin/env bash
set -euo pipefail

# Extracts carry-forward items from previous business day's notes file
# Reads output_dir and stale threshold from config.yaml

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq not installed"
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: config.yaml not found at ${CONFIG}"
  exit 1
fi

if ! OUTPUT_DIR=$(yq '.output_dir' "$CONFIG" 2>&1); then
  echo "ERROR: gather-carryforward: could not read output_dir from config: ${OUTPUT_DIR}" >&2
  exit 1
fi
if [[ "$OUTPUT_DIR" == "null" || -z "$OUTPUT_DIR" ]]; then
  echo "ERROR: output_dir not set in config.yaml"
  exit 1
fi
OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"
if ! STALE_THRESHOLD=$(yq '.planning.stale_threshold_days // 3' "$CONFIG" 2>&1); then
  echo "WARNING: could not read planning.stale_threshold_days from config, using 3: ${STALE_THRESHOLD}" >&2
  STALE_THRESHOLD=3
fi
if [[ ! "$STALE_THRESHOLD" =~ ^[0-9]+$ ]]; then
  echo "WARNING: stale_threshold_days is not numeric ('${STALE_THRESHOLD}'), using 3" >&2
  STALE_THRESHOLD=3
fi

# Walk back through up to 5 business days to find the most recent notes file
# that contains carry_forward items. Day-end is sometimes skipped, so a single
# "previous business day" lookup silently drops all carry-forward context.
MAX_LOOKBACK=5
days_back=0
PREV_DATE=""
frontmatter=""
item_count=0

# Build candidate date by stepping back one calendar day at a time, skipping weekends
candidate=$(date -v-1d +%Y-%m-%d)
while [[ "$days_back" -lt "$MAX_LOOKBACK" ]]; do
  candidate_dow=$(date -j -f "%Y-%m-%d" "$candidate" +%u)
  # Skip Saturday (6) and Sunday (7)
  if [[ "$candidate_dow" -eq 6 || "$candidate_dow" -eq 7 ]]; then
    candidate=$(date -j -v-1d -f "%Y-%m-%d" "$candidate" +%Y-%m-%d)
    continue
  fi

  days_back=$((days_back + 1))
  cand_year=$(date -j -f "%Y-%m-%d" "$candidate" +%Y)
  cand_month=$(date -j -f "%Y-%m-%d" "$candidate" +%m)
  cand_file="${OUTPUT_DIR}/${cand_year}/${cand_month}/${candidate}-notes.md"

  if [[ -f "$cand_file" ]]; then
    first_line=$(head -1 "$cand_file")
    if [[ "$first_line" == "---" ]]; then
      fm=$(awk '/^---$/{c++;next}c==1' "$cand_file")
      if [[ -n "$fm" ]]; then
        if ! count=$(echo "$fm" | yq '.carry_forward | length // 0' 2>&1); then
          echo "WARNING: gather-carryforward: yq failed parsing carry_forward in ${cand_file}: ${count}" >&2
          count=0
        fi
        if [[ ! "$count" =~ ^[0-9]+$ ]]; then
          count=0
        fi
        if [[ "$count" -gt 0 ]]; then
          PREV_DATE="$candidate"
          frontmatter="$fm"
          item_count="$count"
          break
        fi
      fi
    fi
  fi

  candidate=$(date -j -v-1d -f "%Y-%m-%d" "$candidate" +%Y-%m-%d)
done

echo "=== Carry-Forward Items ==="

if [[ -z "$PREV_DATE" ]]; then
  echo "(no notes file with carry-forward items found in last ${MAX_LOOKBACK} business days)"
  exit 0
fi

if [[ "$days_back" -gt 1 ]]; then
  echo "(looked back ${days_back} business days -- last notes with carry-forward: ${PREV_DATE})"
fi

blocked_count=0
stale_count=0
max_days=0

if ! carry_forward_json=$(echo "$frontmatter" | yq -o=json '.carry_forward' 2>&1); then
  echo "WARNING: could not parse carry_forward from ${PREV_DATE} notes: ${carry_forward_json}" >&2
  carry_forward_json="[]"
fi
if ! echo "$carry_forward_json" | jq -e 'type == "array"' &>/dev/null; then
  echo "WARNING: carry_forward in ${PREV_DATE} notes is not a valid array -- skipping items" >&2
  carry_forward_json="[]"
fi

if ! carry_forward_tsv=$(echo "$carry_forward_json" | jq -r '.[] | [.text, (.app_id // "null"), (.blocked | tostring), (.carried_days // 0 | tostring)] | @tsv' 2>&1); then
  echo "WARNING: could not format carry_forward items from ${PREV_DATE} notes: ${carry_forward_tsv}" >&2
  echo "(no carry-forward items emitted -- continuing)"
  carry_forward_tsv=""
fi

while IFS=$'\t' read -r text app_id blocked carried_days; do
  [[ -z "$text" ]] && continue
  if [[ ! "$carried_days" =~ ^[0-9]+$ ]]; then
    echo "WARNING: gather-carryforward: non-numeric carried_days '${carried_days}' for '${text:0:40}' -- treating as 0" >&2
    carried_days=0
  fi
  # Increment here so the LLM copies the value directly into the new plan's frontmatter
  new_days=$((carried_days + 1))

  if [[ "$new_days" -gt "$max_days" ]]; then
    max_days=$new_days
  fi

  # Build the reference tag
  if [[ "$app_id" != "null" && -n "$app_id" ]]; then
    ref="[${app_id}]"
  else
    ref="[work]"
  fi

  # Determine label using the incremented count so stale warnings fire at the right time
  if [[ "$blocked" == "true" ]]; then
    label="BLOCKED (${new_days} days)"
    blocked_count=$((blocked_count + 1))
  elif [[ "$new_days" -ge "$STALE_THRESHOLD" ]]; then
    label="STALE (${new_days} days)"
    stale_count=$((stale_count + 1))
  else
    label="CARRY-OVER (${new_days} days)"
  fi

  echo "${label} ${ref}: ${text}"
  echo "  carried_days: ${new_days}"
done <<< "$carry_forward_tsv"

echo ""
echo "${item_count} items carried forward (${blocked_count} blocked, ${stale_count} stale ${STALE_THRESHOLD}+ days)"

if [[ "$max_days" -ge "$STALE_THRESHOLD" ]]; then
  echo "WARNING: oldest item is ${max_days} days old -- consider escalating or dropping"
fi

# Extract personal tasks from previous day's notes
if ! personal_count=$(echo "$frontmatter" | yq '.personal_tasks | length // 0' 2>&1); then
  personal_count=0
fi
if [[ ! "$personal_count" =~ ^[0-9]+$ ]]; then
  personal_count=0
fi

if [[ "$personal_count" -gt 0 ]]; then
  echo ""
  echo "=== Personal Tasks (carried from ${PREV_DATE}) ==="
  if ! personal_json=$(echo "$frontmatter" | yq -o=json '.personal_tasks' 2>&1); then
    echo "WARNING: could not parse personal_tasks from ${PREV_DATE} notes: ${personal_json}" >&2
    personal_json="[]"
  fi
  if ! echo "$personal_json" | jq -e 'type == "array"' &>/dev/null; then
    echo "WARNING: personal_tasks in ${PREV_DATE} notes is not a valid array -- skipping" >&2
  else
    if ! personal_tsv=$(echo "$personal_json" | jq -r '.[] | [.text, (.time // "null"), (.duration_minutes // "null" | tostring), (.notes // "null")] | @tsv' 2>&1); then
      echo "WARNING: could not format personal_tasks from ${PREV_DATE} notes: ${personal_tsv}" >&2
      personal_tsv=""
    fi
    while IFS=$'\t' read -r pt_text pt_time pt_dur pt_notes; do
      [[ -z "$pt_text" ]] && continue
      line="- ${pt_text}"
      if [[ "$pt_time" != "null" && -n "$pt_time" ]]; then
        line="${line} @ ${pt_time}"
      fi
      if [[ "$pt_dur" != "null" && -n "$pt_dur" ]]; then
        line="${line} (~${pt_dur}min)"
      fi
      if [[ "$pt_notes" != "null" && -n "$pt_notes" ]]; then
        line="${line} — ${pt_notes}"
      fi
      echo "$line"
    done <<< "$personal_tsv"
  fi
fi
