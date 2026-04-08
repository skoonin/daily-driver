#!/usr/bin/env bash
set -euo pipefail

# Reads daily notes/plan files for a date range
# Usage: gather-notes-range.sh START_DATE END_DATE TYPE
#   TYPE: notes | plan | all

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

if [[ $# -ne 3 ]]; then
  echo "Usage: gather-notes-range.sh START_DATE END_DATE TYPE"
  echo "  TYPE: notes | plan | all"
  exit 1
fi

START_DATE="$1"
END_DATE="$2"
TYPE="$3"

if [[ "$TYPE" != "notes" && "$TYPE" != "plan" && "$TYPE" != "all" ]]; then
  echo "ERROR: TYPE must be notes, plan, or all (got: ${TYPE})"
  exit 1
fi

OUTPUT_DIR=$(yq '.output_dir' "$CONFIG")
OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"

if [[ "$TYPE" == "all" ]]; then
  types=("plan" "notes")
else
  types=("$TYPE")
fi

found=0
current="$START_DATE"

while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  year=$(echo "$current" | cut -d- -f1)
  month=$(echo "$current" | cut -d- -f2)

  local dow
  dow=$(date -j -f "%Y-%m-%d" "$current" +%u)

  for t in "${types[@]}"; do
    file="${OUTPUT_DIR}/${year}/${month}/${current}-${t}.md"
    if [[ -f "$file" ]]; then
      echo "=== File: ${current}-${t}.md ==="
      cat "$file"
      echo ""
      found=1
    elif [[ "$dow" -lt 6 ]]; then
      echo "(no ${t} file for ${current})"
    fi
  done

  current=$(date -j -v+1d -f "%Y-%m-%d" "$current" +%Y-%m-%d)
done

if [[ "$found" -eq 0 ]]; then
  echo "(no files found in range ${START_DATE} to ${END_DATE})"
fi
