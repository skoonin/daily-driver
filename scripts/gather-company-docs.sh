#!/usr/bin/env bash
set -euo pipefail

# Reads company docs for any app_id referenced in today's plan.
# Used by check-in and day-end to surface call notes, assessments, and open questions
# without requiring manual file lookups.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh" 2>/dev/null) || OUTPUT_DIR=""

if [[ -z "$OUTPUT_DIR" ]]; then
  echo "(gather-company-docs: output_dir not configured, skipping)"
  exit 0
fi

COMPANIES_DIR="${OUTPUT_DIR}/companies"
if [[ ! -d "$COMPANIES_DIR" ]]; then
  echo "(no company docs found)"
  exit 0
fi

# Extract unique app_ids from today's plan frontmatter
PLAN_SCRIPT="${SCRIPT_DIR}/read-plan-frontmatter.sh"
if [[ ! -x "$PLAN_SCRIPT" ]]; then
  echo "(gather-company-docs: read-plan-frontmatter.sh not found, skipping)"
  exit 0
fi

# Get app_ids from plan items (skip nulls)
APP_IDS=$(bash "$PLAN_SCRIPT" 2>/dev/null | yq '.plan_items[] | select(.app_id != null) | .app_id' 2>/dev/null | sort -u || true)

if [[ -z "$APP_IDS" ]]; then
  echo "(no applications in today's plan)"
  exit 0
fi

found_any=false

while IFS= read -r app_id; do
  [[ -z "$app_id" ]] && continue

  # Find the company doc by searching for files named {app_id}-*.md under companies/
  while IFS= read -r doc_file; do
    if [[ -f "$doc_file" ]]; then
      echo "=== Company Doc: ${app_id} ($(basename "$(dirname "$doc_file")")) ==="
      cat "$doc_file"
      echo ""
      found_any=true
    fi
  done < <(find "$COMPANIES_DIR" -name "${app_id}-*.md" 2>/dev/null)
done <<< "$APP_IDS"

if [[ "$found_any" == false ]]; then
  echo "(no company docs found for today's plan applications)"
fi
