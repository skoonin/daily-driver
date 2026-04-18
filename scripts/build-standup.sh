#!/usr/bin/env bash
set -euo pipefail

# Assembles Y/T/B standup output from YAML frontmatter in daily notes/plan files.
# Invokes Claude only for items flagged --ambiguous (malformed frontmatter or
# missing status values) rather than generating the full standup from prose.
#
# Usage: bash scripts/build-standup.sh [--ambiguous]
#   --ambiguous  pipe only unresolvable items to Claude for normalization

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"
AMBIGUOUS_MODE=false

for arg in "$@"; do
  case "$arg" in
    --ambiguous) AMBIGUOUS_MODE=true ;;
    *) echo "ERROR: unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# --- prerequisites -----------------------------------------------------------

if ! command -v yq &>/dev/null; then
  echo "ERROR: yq not installed" >&2
  exit 1
fi
if ! command -v pbcopy &>/dev/null; then
  echo "ERROR: pbcopy not available (macOS only)" >&2
  exit 1
fi

# --- resolve output_dir ------------------------------------------------------

if ! OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>&1); then
  echo "ERROR: build-standup: could not resolve output_dir: ${OUTPUT_DIR}" >&2
  exit 1
fi

# --- determine yesterday (last workday, skip Sat/Sun) -----------------------

TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)  # 1=Mon ... 7=Sun

case "$DOW" in
  # Monday: last workday was Friday (3 days back)
  1) YESTERDAY=$(date -v-3d +%Y-%m-%d) ;;
  # Sun/Sat shouldn't occur in normal use but fall back gracefully
  7) YESTERDAY=$(date -v-2d +%Y-%m-%d) ;;
  *) YESTERDAY=$(date -v-1d +%Y-%m-%d) ;;
esac

YEST_YEAR=$(date -j -f "%Y-%m-%d" "$YESTERDAY" +%Y)
YEST_MONTH=$(date -j -f "%Y-%m-%d" "$YESTERDAY" +%m)
TODAY_YEAR=$(date +%Y)
TODAY_MONTH=$(date +%m)

NOTES_FILE="${OUTPUT_DIR}/${YEST_YEAR}/${YEST_MONTH}/${YESTERDAY}-notes.md"
PLAN_FILE="${OUTPUT_DIR}/${TODAY_YEAR}/${TODAY_MONTH}/${TODAY}-plan.md"

# --- frontmatter extractor ---------------------------------------------------
# Prints only the YAML block between the first pair of --- delimiters.

extract_frontmatter() {
  local file="$1"
  awk '/^---$/{c++;if(c==2)exit;next}c==1{print}' "$file"
}

# --- parse a section from frontmatter using yq -------------------------------
# $1: YAML text (piped via stdin); $2: yq expression
yq_eval() {
  local expr="$1"
  yq eval "$expr" -
}

# --- collect Y section (completed items from yesterday's notes) -------------

Y_LINES=()
AMBIGUOUS_LINES=()
NOTES_MISSING=false

if [[ ! -f "$NOTES_FILE" ]]; then
  NOTES_MISSING=true
else
  FM=$(extract_frontmatter "$NOTES_FILE")

  # plan_items with status == "done"
  mapfile -t DONE_ITEMS < <(
    echo "$FM" | yq_eval \
      '.plan_items // [] | .[] | select(.status == "done") | .text' 2>/dev/null || true
  )
  for item in "${DONE_ITEMS[@]}"; do
    [[ -n "$item" && "$item" != "null" ]] && Y_LINES+=("$item")
  done

  # carry_forward items that were resolved (status == "done") yesterday
  mapfile -t CF_DONE < <(
    echo "$FM" | yq_eval \
      '.carry_forward // [] | .[] | select(.status == "done") | .text' 2>/dev/null || true
  )
  for item in "${CF_DONE[@]}"; do
    [[ -n "$item" && "$item" != "null" ]] && Y_LINES+=("$item")
  done

  # Flag items whose status is absent or unrecognised as ambiguous
  mapfile -t UNKNOWN_STATUS < <(
    echo "$FM" | yq_eval \
      '.plan_items // [] | .[] | select(.status != "done" and .status != "carry-over" and .status != "dropped" and .status != "planned" and .status != "in-progress") | .text' 2>/dev/null || true
  )
  for item in "${UNKNOWN_STATUS[@]}"; do
    [[ -n "$item" && "$item" != "null" ]] && AMBIGUOUS_LINES+=("[notes:unknown-status] $item")
  done
fi

# --- collect T section (planned items from today's plan) --------------------

T_LINES=()
PLAN_MISSING=false

if [[ ! -f "$PLAN_FILE" ]]; then
  PLAN_MISSING=true
else
  FM_PLAN=$(extract_frontmatter "$PLAN_FILE")

  mapfile -t PLANNED_ITEMS < <(
    echo "$FM_PLAN" | yq_eval \
      '.plan_items // [] | .[] | select(.status == "planned" or .status == "in-progress") | .text' 2>/dev/null || true
  )
  for item in "${PLANNED_ITEMS[@]}"; do
    [[ -n "$item" && "$item" != "null" ]] && T_LINES+=("$item")
  done
fi

# --- collect B section (blocked carry-forward items) ------------------------

B_LINES=()

# Check blockers in yesterday's notes
if [[ -f "$NOTES_FILE" ]]; then
  FM=$(extract_frontmatter "$NOTES_FILE")

  mapfile -t BLOCKED_CF < <(
    echo "$FM" | yq_eval \
      '.carry_forward // [] | .[] | select(.blocked == true) | .text + (if .blocked_reason then " (" + .blocked_reason + ")" else "" end)' 2>/dev/null || true
  )
  for item in "${BLOCKED_CF[@]}"; do
    [[ -n "$item" && "$item" != "null" ]] && B_LINES+=("$item")
  done

  # top-level blockers list (less common but supported by schema)
  mapfile -t TOP_BLOCKERS < <(
    echo "$FM" | yq_eval '.blockers // [] | .[]' 2>/dev/null || true
  )
  for item in "${TOP_BLOCKERS[@]}"; do
    [[ -n "$item" && "$item" != "null" ]] && B_LINES+=("$item")
  done
fi

# Check blockers in today's plan
if [[ -f "$PLAN_FILE" ]]; then
  FM_PLAN=$(extract_frontmatter "$PLAN_FILE")

  mapfile -t PLAN_BLOCKED < <(
    echo "$FM_PLAN" | yq_eval \
      '.carry_forward // [] | .[] | select(.blocked == true) | .text + (if .blocked_reason then " (" + .blocked_reason + ")" else "" end)' 2>/dev/null || true
  )
  for item in "${PLAN_BLOCKED[@]}"; do
    [[ -n "$item" && "$item" != "null" ]] && B_LINES+=("$item")
  done

  mapfile -t PLAN_TOP_BLOCKERS < <(
    echo "$FM_PLAN" | yq_eval '.blockers // [] | .[]' 2>/dev/null || true
  )
  for item in "${PLAN_TOP_BLOCKERS[@]}"; do
    [[ -n "$item" && "$item" != "null" ]] && B_LINES+=("$item")
  done
fi

# --- ambiguous mode: pipe only unclear items to Claude ----------------------

if [[ "$AMBIGUOUS_MODE" == "true" ]]; then
  if [[ "${#AMBIGUOUS_LINES[@]}" -gt 0 ]]; then
    echo "AMBIGUOUS_ITEMS:"
    for item in "${AMBIGUOUS_LINES[@]}"; do
      echo "  - $item"
    done
    echo ""
    echo "Pipe these to Claude with: normalize each item to a one-line completed action."
  fi
  exit 0
fi

# --- assemble standup text --------------------------------------------------

STANDUP=""

STANDUP+="Y:"$'\n'
if [[ "${#Y_LINES[@]}" -gt 0 ]]; then
  for line in "${Y_LINES[@]}"; do
    STANDUP+="- ${line}"$'\n'
  done
elif [[ "$NOTES_MISSING" == "true" ]]; then
  STANDUP+="- (no notes file for ${YESTERDAY})"$'\n'
else
  STANDUP+="- (no completed items recorded)"$'\n'
fi

STANDUP+=""$'\n'
STANDUP+="T:"$'\n'
if [[ "${#T_LINES[@]}" -gt 0 ]]; then
  for line in "${T_LINES[@]}"; do
    STANDUP+="- ${line}"$'\n'
  done
elif [[ "$PLAN_MISSING" == "true" ]]; then
  STANDUP+="- (no plan file for ${TODAY} — run /day-start)"$'\n'
else
  STANDUP+="- (no planned items found)"$'\n'
fi

STANDUP+=""$'\n'
STANDUP+="B:"$'\n'
if [[ "${#B_LINES[@]}" -gt 0 ]]; then
  for line in "${B_LINES[@]}"; do
    STANDUP+="- ${line}"$'\n'
  done
else
  STANDUP+="- None"$'\n'
fi

# --- output and clipboard ---------------------------------------------------

printf '%s' "$STANDUP"
printf '%s' "$STANDUP" | pbcopy
echo ""
echo "(copied to clipboard)"

# --- save if configured -----------------------------------------------------

SAVE=$(yq '.reporting.standup.save // false' "$CONFIG" 2>/dev/null || echo "false")
if [[ "$SAVE" == "true" ]]; then
  SAVE_DIR="${OUTPUT_DIR}/${TODAY_YEAR}/${TODAY_MONTH}"
  mkdir -p "$SAVE_DIR"
  SAVE_FILE="${SAVE_DIR}/${TODAY}-standup.md"
  printf '%s' "$STANDUP" > "$SAVE_FILE"
  echo "(saved to ${SAVE_FILE})"
fi

# --- signal ambiguity for caller to handle ----------------------------------

if [[ "${#AMBIGUOUS_LINES[@]}" -gt 0 ]]; then
  exit 2
fi

exit 0
