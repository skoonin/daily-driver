#!/usr/bin/env bash
set -euo pipefail

# Job application tracker -- CRUD operations on tracker.yaml
# Usage: tracker.sh <command> [args]

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
TRACKER="${OUTPUT_DIR}/tracker.yaml"
FOLLOW_UP_DAYS=$(yq '.tracker.follow_up_days // 7' "$CONFIG")

ensure_tracker() {
  if [[ ! -f "$TRACKER" ]]; then
    mkdir -p "$(dirname "$TRACKER")"
    echo "applications: []" > "$TRACKER"
  fi
}

next_id() {
  local max
  max=$(yq '.applications[].id' "$TRACKER" 2>/dev/null | sed 's/app-//' | sort -n | tail -1)
  if [[ -z "$max" ]]; then
    echo "app-001"
  else
    printf "app-%03d" $((max + 1))
  fi
}

today() {
  date +%Y-%m-%d
}

follow_up_date() {
  local status="${1:-applied}"
  # Prefer status-specific interval; fall back to global follow_up_days
  local days
  days=$(yq ".tracker.follow_up_by_status.${status} // ${FOLLOW_UP_DAYS}" "$CONFIG")
  date -v+"${days}"d +%Y-%m-%d
}

cmd_init() {
  ensure_tracker
  echo "Tracker initialized at ${TRACKER}"
}

cmd_add() {
  local company="${1:?Usage: tracker.sh add COMPANY ROLE URL [SOURCE]}"
  local role="${2:?Usage: tracker.sh add COMPANY ROLE URL [SOURCE]}"
  local url="${3:?Usage: tracker.sh add COMPANY ROLE URL [SOURCE]}"
  local source="${4:-other}"

  ensure_tracker

  local id
  id=$(next_id)
  local dt
  dt=$(today)
  local fu
  fu=$(follow_up_date "applied")

  yq -i ".applications += [{
    \"id\": \"${id}\",
    \"company\": \"${company}\",
    \"role\": \"${role}\",
    \"url\": \"${url}\",
    \"status\": \"applied\",
    \"date_applied\": \"${dt}\",
    \"last_activity\": \"${dt}\",
    \"follow_up_date\": \"${fu}\",
    \"notes\": \"\",
    \"source\": \"${source}\"
  }]" "$TRACKER"

  echo "${id} | ${company} | ${role} | applied | follow-up: ${fu}"
}

cmd_update() {
  local app_id="${1:?Usage: tracker.sh update APP-ID FIELD VALUE}"
  local field="${2:?Usage: tracker.sh update APP-ID FIELD VALUE}"
  local value="${3:?Usage: tracker.sh update APP-ID FIELD VALUE}"

  ensure_tracker

  # Verify the app exists
  local exists
  exists=$(yq ".applications[] | select(.id == \"${app_id}\") | .id" "$TRACKER")
  if [[ -z "$exists" ]]; then
    echo "ERROR: ${app_id} not found"
    exit 1
  fi

  yq -i "(.applications[] | select(.id == \"${app_id}\")).${field} = \"${value}\"" "$TRACKER"
  yq -i "(.applications[] | select(.id == \"${app_id}\")).last_activity = \"$(today)\"" "$TRACKER"

  # Recalculate follow-up date on status change using status-specific interval
  if [[ "$field" == "status" ]]; then
    local fu
    fu=$(follow_up_date "$value")
    yq -i "(.applications[] | select(.id == \"${app_id}\")).follow_up_date = \"${fu}\"" "$TRACKER"
  fi

  echo "Updated ${app_id}: ${field} = ${value}"
}

cmd_list() {
  local filter_status="${1:-}"

  ensure_tracker

  local count
  count=$(yq '.applications | length' "$TRACKER")
  if [[ "$count" -eq 0 ]]; then
    echo "(no applications tracked yet)"
    return
  fi

  local dt
  dt=$(today)
  local dt_epoch
  dt_epoch=$(date -j -f "%Y-%m-%d" "$dt" +%s)

  printf "%-8s | %-20s | %-25s | %-14s | %s\n" "ID" "Company" "Role" "Status" "Days"
  printf "%s\n" "---------|----------------------|---------------------------|----------------|------"

  while IFS=$'\t' read -r id company role status last_activity; do
    if [[ -n "$filter_status" && "$status" != "$filter_status" ]]; then
      continue
    fi

    local la_epoch days_since
    la_epoch=$(date -j -f "%Y-%m-%d" "$last_activity" +%s 2>/dev/null) || la_epoch=$dt_epoch
    days_since=$(( (dt_epoch - la_epoch) / 86400 ))

    printf "%-8s | %-20s | %-25s | %-14s | %dd\n" "$id" "$company" "$role" "$status" "$days_since"
  done < <(yq -o=json '.applications' "$TRACKER" | jq -r '.[] | [.id, .company, .role, .status, .last_activity] | @tsv')
}

cmd_stats() {
  ensure_tracker

  local days_since_mon=$(( ($(date +%u) - 1) ))
  local week_start
  week_start=$(date -v-${days_since_mon}d +%Y-%m-%d)

  local total researching applied screening interviewing offer rejected ghosted this_week
  local stats_line
  stats_line=$(yq -o=json '.applications' "$TRACKER" | jq -r --arg ws "$week_start" '[
    length,
    ([.[] | select(.status == "researching")] | length),
    ([.[] | select(.status == "applied")] | length),
    ([.[] | select(.status == "screening")] | length),
    ([.[] | select(.status == "interviewing")] | length),
    ([.[] | select(.status == "offer")] | length),
    ([.[] | select(.status == "rejected")] | length),
    ([.[] | select(.status == "ghosted")] | length),
    ([.[] | select(.date_applied >= $ws)] | length)
  ] | @tsv')
  IFS=$'\t' read -r total researching applied screening interviewing offer rejected ghosted this_week <<< "$stats_line"

  local active=$((researching + applied + screening + interviewing + offer))

  echo "Total: ${total} | Active: ${active}"
  echo "Pipeline: researching=${researching} applied=${applied} screening=${screening} interviewing=${interviewing} offer=${offer}"
  echo "Closed: rejected=${rejected} ghosted=${ghosted}"
  echo "Applied this week: ${this_week}"
}

cmd_get() {
  local app_id="${1:?Usage: tracker.sh get APP-ID}"

  ensure_tracker

  local result
  result=$(yq ".applications[] | select(.id == \"${app_id}\")" "$TRACKER")
  if [[ -z "$result" ]]; then
    echo "ERROR: ${app_id} not found"
    exit 1
  fi
  echo "$result"
}

cmd_audit() {
  ensure_tracker

  local jobs_csv="${OUTPUT_DIR}/jobs.csv"
  if [[ ! -f "$jobs_csv" ]]; then
    echo "ERROR: jobs.csv not found at ${jobs_csv}"
    exit 1
  fi

  local issues=0

  # Applied statuses that should exist in both systems
  local active_statuses=("applied" "screening" "interviewing" "offer")

  # Build tracker company list (lowercased) for comparison
  local tracker_companies
  tracker_companies=$(yq -o=json '.applications' "$TRACKER" | jq -r '[.[] | {id: .id, company: (.company | ascii_downcase), role: .role, status: .status}]')

  # Check applied jobs in jobs.csv that have no tracker entry
  echo "=== Applied jobs.csv rows with no tracker entry ==="
  local csv_issues=0
  while IFS=',' read -r num company product role comp location fit source date_found status date_applied link notes; do
    # Strip quotes
    company=$(echo "$company" | tr -d '"' | xargs)
    role=$(echo "$role" | tr -d '"' | xargs)
    status=$(echo "$status" | tr -d '"' | xargs)

    local matched=false
    for check_status in "${active_statuses[@]}"; do
      if [[ "$status" == "$check_status" ]]; then
        matched=true
        break
      fi
    done
    [[ "$matched" == "false" ]] && continue

    local company_lower
    company_lower=$(echo "$company" | tr '[:upper:]' '[:lower:]')
    local found_in_tracker
    found_in_tracker=$(echo "$tracker_companies" | jq -r --arg c "$company_lower" '.[] | select(.company == $c) | .id' | head -1)

    if [[ -z "$found_in_tracker" ]]; then
      echo "  MISSING: ${company} / ${role} (jobs.csv status: ${status})"
      csv_issues=$((csv_issues + 1))
      issues=$((issues + 1))
    fi
  done < <(tail -n +2 "$jobs_csv")

  [[ "$csv_issues" -eq 0 ]] && echo "  (none)"

  # Check tracker entries with no jobs.csv row
  echo ""
  echo "=== Tracker entries with no jobs.csv row ==="
  local tracker_issues=0
  while IFS=$'\t' read -r id company role status; do
    local company_lower
    company_lower=$(echo "$company" | tr '[:upper:]' '[:lower:]')
    local found_in_csv=false

    while IFS=',' read -r num csv_company rest; do
      local csv_company_lower
      csv_company_lower=$(echo "$csv_company" | tr -d '"' | tr '[:upper:]' '[:lower:]' | xargs)
      if [[ "$csv_company_lower" == "$company_lower" ]]; then
        found_in_csv=true
        break
      fi
    done < <(tail -n +2 "$jobs_csv")

    if [[ "$found_in_csv" == "false" ]]; then
      echo "  MISSING: ${id} | ${company} / ${role} (tracker status: ${status})"
      tracker_issues=$((tracker_issues + 1))
      issues=$((issues + 1))
    fi
  done < <(yq -o=json '.applications' "$TRACKER" | jq -r '.[] | [.id, .company, .role, .status] | @tsv')

  [[ "$tracker_issues" -eq 0 ]] && echo "  (none)"

  # Check status mismatches for companies present in both
  echo ""
  echo "=== Status mismatches ==="
  local mismatch_issues=0
  while IFS=$'\t' read -r id company role tracker_status; do
    local company_lower
    company_lower=$(echo "$company" | tr '[:upper:]' '[:lower:]')

    while IFS=',' read -r num csv_company product csv_role comp location fit source date_found csv_status rest; do
      local csv_company_lower
      csv_company_lower=$(echo "$csv_company" | tr -d '"' | tr '[:upper:]' '[:lower:]' | xargs)
      csv_status=$(echo "$csv_status" | tr -d '"' | xargs)

      if [[ "$csv_company_lower" == "$company_lower" && "$csv_status" != "$tracker_status" ]]; then
        echo "  MISMATCH: ${company} | jobs.csv=${csv_status} tracker=${tracker_status} (${id})"
        mismatch_issues=$((mismatch_issues + 1))
        issues=$((issues + 1))
        break
      fi
    done < <(tail -n +2 "$jobs_csv")
  done < <(yq -o=json '.applications' "$TRACKER" | jq -r '.[] | [.id, .company, .role, .status] | @tsv')

  [[ "$mismatch_issues" -eq 0 ]] && echo "  (none)"

  echo ""
  if [[ "$issues" -eq 0 ]]; then
    echo "OK: tracker and jobs.csv are in sync"
  else
    echo "Found ${issues} discrepancy(s)"
    exit 1
  fi
}

cmd_follow_ups() {
  ensure_tracker

  local dt
  dt=$(today)
  local count
  count=$(yq '.applications | length' "$TRACKER")

  if [[ "$count" -eq 0 ]]; then
    echo "(no applications tracked yet)"
    return
  fi

  local found=0
  local dt_epoch
  dt_epoch=$(date -j -f "%Y-%m-%d" "$dt" +%s)

  while IFS=$'\t' read -r id company role status last_activity follow_up; do
    if [[ "$follow_up" == "null" || -z "$follow_up" ]]; then
      continue
    fi

    local fu_epoch
    fu_epoch=$(date -j -f "%Y-%m-%d" "$follow_up" +%s 2>/dev/null) || continue

    if [[ "$fu_epoch" -le "$dt_epoch" ]]; then
      local la_epoch days_since
      la_epoch=$(date -j -f "%Y-%m-%d" "$last_activity" +%s 2>/dev/null) || la_epoch=$dt_epoch
      days_since=$(( (dt_epoch - la_epoch) / 86400 ))

      local label="due"
      if [[ "$fu_epoch" -lt "$dt_epoch" ]]; then
        label="OVERDUE"
      fi

      printf "%s | %-20s | %-25s | %-10s | %dd since activity | follow-up: %s (%s)\n" \
        "$id" "$company" "$role" "$status" "$days_since" "$follow_up" "$label"
      found=$((found + 1))
    fi
  done < <(yq -o=json '.applications' "$TRACKER" | jq -r '.[] | select(.status == "applied" or .status == "screening" or .status == "interviewing") | [.id, .company, .role, .status, .last_activity, .follow_up_date] | @tsv')

  if [[ "$found" -eq 0 ]]; then
    echo "(no follow-ups due)"
  fi
}

usage() {
  cat <<EOF
Usage: tracker.sh <command> [args]

Commands:
  init                              Initialize tracker file
  add COMPANY ROLE URL [SOURCE]     Add application (source: linkedin|indeed|company|referral|other)
  update APP-ID FIELD VALUE         Update a field (status, notes, follow_up_date, etc.)
  list [STATUS]                     List all or filtered by status
  stats                             Pipeline counts and weekly summary
  get APP-ID                        Show single application as YAML
  follow-ups                        List applications with overdue follow-ups
  audit                             Cross-reference tracker.yaml and jobs.csv for discrepancies
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

case "$1" in
  init)       cmd_init ;;
  add)        shift; cmd_add "$@" ;;
  update)     shift; cmd_update "$@" ;;
  list)       shift; cmd_list "${1:-}" ;;
  stats)      cmd_stats ;;
  get)        shift; cmd_get "$@" ;;
  follow-ups) cmd_follow_ups ;;
  audit)      cmd_audit ;;
  *)          echo "ERROR: unknown command: $1"; usage; exit 1 ;;
esac
