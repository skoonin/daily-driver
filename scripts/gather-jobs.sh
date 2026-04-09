#!/usr/bin/env bash
set -euo pipefail

# Generates a dated job-queue markdown file with clickable search URLs per source x role.
# Sends a macOS notification when complete.
# Phase 1: URL generation only. Phase 2 will add Playwright-based extraction.

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
TODAY=$(date +%Y-%m-%d)
QUEUE_FILE="${OUTPUT_DIR}/job-queue-${TODAY}.md"

mkdir -p "$OUTPUT_DIR"

# URL-encode role for query strings: spaces become +
encode_role() {
  printf '%s' "$1" | sed 's/ /+/g'
}

# URL slug for remoteok: lowercase, spaces become hyphens
encode_role_slug() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/ /-/g'
}

# Count enabled sources for the summary line
board_count=$(yq '[.job_search.sources.boards[] | select(.enabled == true)] | length' "$CONFIG")
company_count=$(yq '[.job_search.sources.companies[] | select(.enabled == true)] | length' "$CONFIG")
source_count=$((board_count + company_count))

# Collect roles into a bash array
mapfile -t roles < <(yq '.job_search.roles[]' "$CONFIG")

# Begin writing the queue file
{
  echo "# Job Queue - ${TODAY}"
  echo ""
} > "$QUEUE_FILE"

# We'll count links as we write; use a temp count file to avoid subshell issues
link_count=0

# --- Job Boards ---
{
  echo "## Job Boards"
  echo ""
} >> "$QUEUE_FILE"

board_ids_enabled=$(yq '[.job_search.sources.boards[] | select(.enabled == true) | .id] | .[]' "$CONFIG")

while IFS= read -r board_id; do
  board_name=$(yq ".job_search.sources.boards[] | select(.id == \"${board_id}\") | .name" "$CONFIG")
  search_url=$(yq ".job_search.sources.boards[] | select(.id == \"${board_id}\") | .search_url // \"\"" "$CONFIG")
  static_url=$(yq ".job_search.sources.boards[] | select(.id == \"${board_id}\") | .static_url // \"\"" "$CONFIG")

  {
    echo "### ${board_name}"
  } >> "$QUEUE_FILE"

  if [[ -n "$search_url" && "$search_url" != "null" ]]; then
    # One link per role (LinkedIn, Indeed, Wellfound)
    for role in "${roles[@]}"; do
      encoded=$(encode_role "$role")
      url="${search_url//\{role\}/${encoded}}"
      echo "- [${role}](${url})" >> "$QUEUE_FILE"
      link_count=$((link_count + 1))
    done
  elif [[ -n "$static_url" && "$static_url" != "null" ]]; then
    if [[ "$static_url" == *"{role}"* ]]; then
      # Static URL with role placeholder (weworkremotely, remoteok)
      for role in "${roles[@]}"; do
        if [[ "$board_id" == "remoteok" ]]; then
          encoded=$(encode_role_slug "$role")
        else
          encoded=$(encode_role "$role")
        fi
        url="${static_url//\{role\}/${encoded}}"
        echo "- [${role}](${url})" >> "$QUEUE_FILE"
        link_count=$((link_count + 1))
      done
    else
      # Static URL without role placeholder (HN Who's Hiring)
      echo "- [Current thread (whoishiring)](${static_url})" >> "$QUEUE_FILE"
      link_count=$((link_count + 1))
    fi
  fi

  echo "" >> "$QUEUE_FILE"
done <<< "$board_ids_enabled"

# --- Companies ---
company_ids_enabled=$(yq '[.job_search.sources.companies[] | select(.enabled == true) | .id] | .[]' "$CONFIG")
enabled_company_count=$(echo "$company_ids_enabled" | grep -c . 2>/dev/null || echo 0)

if [[ "$enabled_company_count" -gt 0 ]]; then
  {
    echo "## Companies"
    echo ""
  } >> "$QUEUE_FILE"

  while IFS= read -r company_id; do
    company_name=$(yq ".job_search.sources.companies[] | select(.id == \"${company_id}\") | .name" "$CONFIG")
    careers_url=$(yq ".job_search.sources.companies[] | select(.id == \"${company_id}\") | .careers_url" "$CONFIG")

    {
      echo "### ${company_name}"
      echo "- [Careers](${careers_url})"
      echo ""
    } >> "$QUEUE_FILE"
    link_count=$((link_count + 1))
  done <<< "$company_ids_enabled"
fi

# Prepend the summary line now that we have the final link count
summary="${source_count} sources | ${link_count} searches ready for review."
tmp_file=$(mktemp)
{
  head -2 "$QUEUE_FILE"
  echo "${summary}"
  echo "To add jobs found: bash scripts/tracker.sh add"
  echo ""
  tail -n +3 "$QUEUE_FILE"
} > "$tmp_file"
mv "$tmp_file" "$QUEUE_FILE"

echo "Job queue written: ${QUEUE_FILE}"
echo "${source_count} sources | ${link_count} searches"

# Phase 2: scrape job listings and append new rows to jobs.csv
PYTHON=$(command -v python3 || true)
SCRAPER="${SCRIPT_DIR}/scrape-jobs.py"

if [[ -f "$SCRAPER" && -n "$PYTHON" ]]; then
  echo "Running job scraper..."
  "$PYTHON" "$SCRAPER" --config "$CONFIG" 2>&1 | tee -a /tmp/daily-driver-gather-jobs.log
  scraper_exit=${PIPESTATUS[0]}
  if [[ $scraper_exit -ne 0 ]]; then
    echo "WARNING: scraper exited with code ${scraper_exit} — check /tmp/daily-driver-gather-jobs.log"
  fi
else
  echo "Skipping Phase 2: scrape-jobs.py or python3 not found"
fi

# macOS notification — terminal-notifier required (brew install terminal-notifier)
# --open opens the queue file directly when the notification is clicked
if command -v terminal-notifier &>/dev/null; then
  terminal-notifier \
    -title "Daily Job Search" \
    -subtitle "Review job-queue-${TODAY}.md" \
    -message "${source_count} sources, ${link_count} searches ready" \
    -open "file://${QUEUE_FILE}"
else
  osascript -e "display notification \"${source_count} sources, ${link_count} searches ready\" with title \"Daily Job Search\" subtitle \"Review job-queue-${TODAY}.md\""
fi
