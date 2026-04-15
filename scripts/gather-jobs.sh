#!/opt/homebrew/bin/bash
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

OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh") || exit 1
TODAY=$(date +%Y-%m-%d)
QUEUE_FILE="${OUTPUT_DIR}/job-queue-${TODAY}.md"

mkdir -p "$OUTPUT_DIR"

# Remove previous days' queue files — only today's is relevant
find "$OUTPUT_DIR" -maxdepth 1 -name "job-queue-*.md" ! -name "job-queue-${TODAY}.md" -delete

# URL-encode role for query strings: spaces become +
encode_role() {
  printf '%s' "$1" | sed 's/ /+/g; s|/|%2F|g'
}

# URL slug for remoteok: lowercase, spaces become hyphens
encode_role_slug() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's|/|-|g; s/ /-/g'
}

# Count enabled sources for the summary line
if ! board_count=$(yq '[.job_search.sources.boards[] | select(.enabled == true)] | length' "$CONFIG" 2>/dev/null); then
  echo "ERROR: could not read boards from config" >&2
  exit 1
fi
if [[ "$board_count" == "null" || -z "$board_count" ]]; then
  echo "ERROR: job_search.sources.boards not configured in config.yaml" >&2
  exit 1
fi
if [[ ! "$board_count" =~ ^[0-9]+$ ]]; then
  echo "ERROR: non-numeric board count from config: '${board_count}'" >&2
  exit 1
fi

if ! company_count=$(yq '[.job_search.sources.companies[] | select(.enabled == true)] | length' "$CONFIG" 2>/dev/null); then
  echo "ERROR: could not read companies from config" >&2
  exit 1
fi
company_count="${company_count:-0}"
if [[ "$company_count" == "null" ]]; then
  company_count=0
fi
if [[ ! "$company_count" =~ ^[0-9]+$ ]]; then
  company_count=0
fi
source_count=$((board_count + company_count))

# Collect roles into a bash array
if ! roles_raw=$(yq '.job_search.roles[]' "$CONFIG" 2>/dev/null); then
  echo "ERROR: could not read job_search.roles from config" >&2
  exit 1
fi
mapfile -t roles <<< "$roles_raw"
if [[ "${#roles[@]}" -eq 0 || -z "${roles[0]}" ]]; then
  echo "ERROR: job_search.roles is empty or missing in config.yaml -- cannot generate search links" >&2
  exit 1
fi

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

if ! board_ids_enabled=$(yq '[.job_search.sources.boards[] | select(.enabled == true) | .id] | .[]' "$CONFIG" 2>/dev/null); then
  echo "ERROR: could not list enabled board IDs from config" >&2
  exit 1
fi

if [[ -z "$board_ids_enabled" ]]; then
  echo "(no enabled boards)" >> "$QUEUE_FILE"
fi

while IFS= read -r board_id; do
  [[ -z "$board_id" ]] && continue
  if ! board_name=$(yq ".job_search.sources.boards[] | select(.id == \"${board_id}\") | .name" "$CONFIG" 2>/dev/null); then
    echo "WARNING: skipping board '${board_id}': yq failed" >&2
    continue
  fi
  if ! search_url=$(yq ".job_search.sources.boards[] | select(.id == \"${board_id}\") | .search_url // \"\"" "$CONFIG" 2>/dev/null); then
    echo "WARNING: gather-jobs: could not read search_url for board '${board_id}'" >&2
    search_url=""
  fi
  if ! static_url=$(yq ".job_search.sources.boards[] | select(.id == \"${board_id}\") | .static_url // \"\"" "$CONFIG" 2>/dev/null); then
    echo "WARNING: gather-jobs: could not read static_url for board '${board_id}'" >&2
    static_url=""
  fi

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
if ! company_ids_enabled=$(yq '[.job_search.sources.companies[] | select(.enabled == true) | .id] | .[]' "$CONFIG" 2>/dev/null); then
  echo "ERROR: could not list enabled company IDs from config" >&2
  exit 1
fi
enabled_company_count="$company_count"

if [[ "$enabled_company_count" -gt 0 ]]; then
  {
    echo "## Companies"
    echo ""
  } >> "$QUEUE_FILE"

  while IFS= read -r company_id; do
    [[ -z "$company_id" ]] && continue
    if ! company_name=$(yq ".job_search.sources.companies[] | select(.id == \"${company_id}\") | .name" "$CONFIG" 2>/dev/null); then
      echo "WARNING: skipping company '${company_id}': yq failed" >&2
      continue
    fi
    if ! careers_url=$(yq ".job_search.sources.companies[] | select(.id == \"${company_id}\") | .careers_url" "$CONFIG" 2>/dev/null); then
      echo "WARNING: gather-jobs: could not read careers_url for company '${company_id}'" >&2
      careers_url=""
    fi

    {
      echo "### ${company_name}"
      if [[ -n "$careers_url" && "$careers_url" != "null" ]]; then
        echo "- [Careers](${careers_url})"
        link_count=$((link_count + 1))
      else
        echo "- (careers URL unavailable -- check config)"
      fi
      echo ""
    } >> "$QUEUE_FILE"
  done <<< "$company_ids_enabled"
fi

# Prepend the summary line now that we have the final link count
summary="${source_count} sources | ${link_count} searches ready for review."
tmp_file=$(mktemp)
trap 'rm -f "$tmp_file"' EXIT
if ! { head -2 "$QUEUE_FILE"; echo "${summary}"; echo "To add jobs found: bash scripts/tracker.sh add"; echo ""; tail -n +3 "$QUEUE_FILE"; } > "$tmp_file"; then
  echo "ERROR: failed to assemble final queue file" >&2
  exit 1
fi
if ! mv "$tmp_file" "$QUEUE_FILE"; then
  echo "ERROR: gather-jobs: failed to write final queue file to ${QUEUE_FILE}" >&2
  rm -f "$tmp_file"
  exit 1
fi
trap - EXIT

echo "Job queue written: ${QUEUE_FILE}"
echo "${source_count} sources | ${link_count} searches"

# Phase 2: scrape job listings and append new rows to jobs.csv
PYTHON=$(command -v python3 || true)
SCRAPER="${SCRIPT_DIR}/scrape-jobs.py"

scraper_exit=0
if [[ -f "$SCRAPER" && -n "$PYTHON" ]]; then
  echo "Running job scraper..."
  set +e
  "$PYTHON" "$SCRAPER" --config "$CONFIG" 2>&1 | tee -a "${SCRIPT_DIR}/../logs/gather-jobs.log"
  scraper_exit=${PIPESTATUS[0]}
  set -e
  if [[ $scraper_exit -ne 0 ]]; then
    echo "WARNING: scraper exited with code ${scraper_exit} — check logs/gather-jobs.log"
  fi
else
  echo "Skipping Phase 2: scrape-jobs.py or python3 not found"
fi

# macOS notification — terminal-notifier required (brew install terminal-notifier)
# --open opens the queue file directly when the notification is clicked
if [[ $scraper_exit -ne 0 ]]; then
  notify_msg="${source_count} sources ready. SCRAPER FAILED (exit ${scraper_exit})"
else
  notify_msg="${source_count} sources, ${link_count} searches ready"
fi

if command -v terminal-notifier &>/dev/null; then
  terminal-notifier \
    -title "Daily Job Search" \
    -subtitle "Review job-queue-${TODAY}.md" \
    -message "$notify_msg" \
    -open "file://${QUEUE_FILE}" || echo "WARNING: terminal-notifier failed (notification not sent)" >&2
else
  osascript -e "display notification \"${notify_msg}\" with title \"Daily Job Search\" subtitle \"Review job-queue-${TODAY}.md\"" \
    || echo "WARNING: osascript notification failed" >&2
fi
