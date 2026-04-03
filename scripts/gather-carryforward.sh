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

OUTPUT_DIR=$(yq '.output_dir' "$CONFIG")
OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"
STALE_THRESHOLD=$(yq '.planning.stale_threshold_days // 3' "$CONFIG")

# Resolve previous business day (Monday -> Friday, else -> yesterday)
dow=$(date +%u)
if [[ "$dow" -eq 1 ]]; then
  PREV_DATE=$(date -v-3d +%Y-%m-%d)
else
  PREV_DATE=$(date -v-1d +%Y-%m-%d)
fi

PREV_YEAR=$(date -j -f "%Y-%m-%d" "$PREV_DATE" +%Y)
PREV_MONTH=$(date -j -f "%Y-%m-%d" "$PREV_DATE" +%m)
NOTES_FILE="${OUTPUT_DIR}/${PREV_YEAR}/${PREV_MONTH}/${PREV_DATE}-notes.md"

echo "=== Carry-Forward Items ==="

if [[ ! -f "$NOTES_FILE" ]]; then
  echo "(no notes file for ${PREV_DATE})"
  exit 0
fi

# Verify file has YAML frontmatter
first_line=$(head -1 "$NOTES_FILE")
if [[ "$first_line" != "---" ]]; then
  echo "(no frontmatter in ${PREV_DATE} notes)"
  exit 0
fi

# Extract frontmatter between --- delimiters
frontmatter=$(awk '/^---$/{c++;next}c==1' "$NOTES_FILE")

if [[ -z "$frontmatter" ]]; then
  echo "(empty frontmatter in ${PREV_DATE} notes)"
  exit 0
fi

item_count=$(echo "$frontmatter" | yq '.carry_forward | length // 0')

if [[ "$item_count" -eq 0 ]]; then
  echo "(no carry-forward items from ${PREV_DATE})"
  exit 0
fi

blocked_count=0
stale_count=0
max_days=0

for i in $(seq 0 $((item_count - 1))); do
  text=$(echo "$frontmatter" | yq ".carry_forward[$i].text")
  jira=$(echo "$frontmatter" | yq ".carry_forward[$i].jira")
  blocked=$(echo "$frontmatter" | yq ".carry_forward[$i].blocked")
  carried_days=$(echo "$frontmatter" | yq ".carry_forward[$i].carried_days // 1")

  if [[ "$carried_days" -gt "$max_days" ]]; then
    max_days=$carried_days
  fi

  # Build the reference tag
  if [[ "$jira" != "null" && -n "$jira" ]]; then
    ref="[${jira}]"
  else
    ref="[work]"
  fi

  # Determine label
  if [[ "$blocked" == "true" ]]; then
    label="BLOCKED (${carried_days} days)"
    blocked_count=$((blocked_count + 1))
  elif [[ "$carried_days" -ge "$STALE_THRESHOLD" ]]; then
    label="STALE (${carried_days} days)"
    stale_count=$((stale_count + 1))
  else
    label="CARRY-OVER (${carried_days} days)"
  fi

  echo "${label} ${ref}: ${text}"
done

echo ""
echo "${item_count} items carried forward (${blocked_count} blocked, ${stale_count} stale ${STALE_THRESHOLD}+ days)"

if [[ "$max_days" -ge "$STALE_THRESHOLD" ]]; then
  echo "WARNING: oldest item is ${max_days} days old -- consider escalating or dropping"
fi
