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

if ! OUTPUT_DIR=$(yq '.output_dir' "$CONFIG" 2>&1); then
  echo "ERROR: tracker: could not read output_dir from config: ${OUTPUT_DIR}" >&2
  exit 1
fi
if [[ "$OUTPUT_DIR" == "null" || -z "$OUTPUT_DIR" ]]; then
  echo "ERROR: output_dir not set in config.yaml"
  exit 1
fi
OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"
TRACKER="${OUTPUT_DIR}/tracker.yaml"
if ! FOLLOW_UP_DAYS=$(yq '.tracker.follow_up_days // 7' "$CONFIG" 2>&1); then
  echo "WARNING: tracker: could not read follow_up_days from config, using 7: ${FOLLOW_UP_DAYS}" >&2
  FOLLOW_UP_DAYS=7
fi
if [[ ! "$FOLLOW_UP_DAYS" =~ ^[0-9]+$ ]]; then
  echo "WARNING: tracker.follow_up_days is not numeric ('${FOLLOW_UP_DAYS}'), using 7" >&2
  FOLLOW_UP_DAYS=7
fi

ensure_tracker() {
  if [[ ! -f "$TRACKER" ]]; then
    mkdir -p "$(dirname "$TRACKER")"
    echo "applications: []" > "$TRACKER"
  fi
}

next_id() {
  local raw
  if ! raw=$(yq '.applications[].id' "$TRACKER" 2>&1); then
    echo "ERROR: next_id: could not read tracker IDs: ${raw}" >&2
    exit 1
  fi
  local max
  max=$(printf '%s\n' "$raw" | { grep -E '^app-[0-9]+$' || true; } | sed 's/app-//' | sort -n | tail -1)
  if [[ -z "$max" ]]; then
    echo "app-001"
  elif [[ ! "$max" =~ ^[0-9]+$ ]]; then
    echo "ERROR: malformed IDs in tracker — cannot determine next ID" >&2
    exit 1
  else
    printf "app-%03d" $((10#$max + 1))
  fi
}

today() {
  date +%Y-%m-%d
}

follow_up_date() {
  local status="${1:-applied}"
  # Prefer status-specific interval; fall back to global follow_up_days
  local days
  if ! days=$(STATUS="$status" yq ".tracker.follow_up_by_status.[strenv(STATUS)] // ${FOLLOW_UP_DAYS}" "$CONFIG" 2>&1); then
    echo "WARNING: follow_up_date: yq failed reading config, using ${FOLLOW_UP_DAYS} days: ${days}" >&2
    days="$FOLLOW_UP_DAYS"
  fi
  if [[ -z "$days" || "$days" == "null" || ! "$days" =~ ^[0-9]+$ ]]; then
    days="$FOLLOW_UP_DAYS"
  fi
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

  if ! ID="$id" COMPANY="$company" ROLE="$role" URL="$url" DT="$dt" FU="$fu" SOURCE="$source" \
    yq -i '.applications += [{"id": strenv(ID), "company": strenv(COMPANY), "role": strenv(ROLE), "url": strenv(URL), "status": "applied", "date_applied": strenv(DT), "last_activity": strenv(DT), "follow_up_date": strenv(FU), "notes": "", "source": strenv(SOURCE)}]' "$TRACKER" 2>&1; then
    echo "ERROR: tracker add: yq failed to append application" >&2
    exit 1
  fi

  echo "${id} | ${company} | ${role} | applied | follow-up: ${fu}"
}

cmd_update() {
  local app_id="${1:?Usage: tracker.sh update APP-ID FIELD VALUE}"
  local field="${2:?Usage: tracker.sh update APP-ID FIELD VALUE}"
  local value="${3:?Usage: tracker.sh update APP-ID FIELD VALUE}"

  ensure_tracker

  # Validate field name against known tracker fields
  case "$field" in
    status|notes|follow_up_date|url|source|role|company|date_applied) ;;
    *) echo "ERROR: invalid field '${field}' -- allowed: status notes follow_up_date url source role company date_applied" >&2; exit 1 ;;
  esac

  # Verify the app exists
  local exists
  if ! exists=$(APPID="$app_id" yq '.applications[] | select(.id == strenv(APPID)) | .id' "$TRACKER" 2>&1); then
    echo "ERROR: tracker update: could not read tracker: ${exists}" >&2
    exit 1
  fi
  if [[ -z "$exists" ]]; then
    echo "ERROR: ${app_id} not found"
    exit 1
  fi

  local yq_err
  if ! yq_err=$(VALUE="$value" APPID="$app_id" FIELD="$field" yq -i '(.applications[] | select(.id == strenv(APPID)))[strenv(FIELD)] = strenv(VALUE)' "$TRACKER" 2>&1); then
    echo "ERROR: tracker update: yq failed to set ${field}: ${yq_err}" >&2
    exit 1
  fi
  if ! yq_err=$(APPID="$app_id" ACTIVITY="$(today)" yq -i '(.applications[] | select(.id == strenv(APPID))).last_activity = strenv(ACTIVITY)' "$TRACKER" 2>&1); then
    echo "ERROR: tracker update: yq failed to set last_activity: ${yq_err}" >&2
    exit 1
  fi

  # Recalculate follow-up date on status change using status-specific interval
  if [[ "$field" == "status" ]]; then
    local fu
    fu=$(follow_up_date "$value")
    if ! yq_err=$(APPID="$app_id" FU="$fu" yq -i '(.applications[] | select(.id == strenv(APPID))).follow_up_date = strenv(FU)' "$TRACKER" 2>&1); then
      echo "ERROR: tracker update: yq failed to set follow_up_date: ${yq_err}" >&2
      exit 1
    fi
  fi

  echo "Updated ${app_id}: ${field} = ${value}"
}

cmd_list() {
  local filter_status="${1:-}"

  ensure_tracker

  local count
  if ! count=$(yq '.applications | length' "$TRACKER" 2>&1); then
    echo "ERROR: tracker list: could not read applications from ${TRACKER}: ${count}" >&2
    return 1
  fi
  if [[ "$count" -eq 0 ]]; then
    echo "(no applications tracked yet)"
    return
  fi

  local dt
  dt=$(today)
  local dt_epoch
  if ! dt_epoch=$(date -j -f "%Y-%m-%d" "$dt" +%s 2>&1); then
    echo "ERROR: tracker list: invalid date '${dt}': ${dt_epoch}" >&2
    return 1
  fi

  local apps_json
  if ! apps_json=$(yq -o=json '.applications' "$TRACKER" 2>&1); then
    echo "ERROR: tracker list: yq failed reading tracker: ${apps_json}" >&2
    return 1
  fi
  local app_tsv
  if ! app_tsv=$(echo "$apps_json" | jq -r '.[] | [.id, .company, .role, .status, .last_activity] | @tsv' 2>&1); then
    echo "ERROR: tracker list: could not parse applications from ${TRACKER}" >&2
    return 1
  fi

  printf "%-8s | %-20s | %-25s | %-14s | %s\n" "ID" "Company" "Role" "Status" "Days"
  printf "%s\n" "---------|----------------------|---------------------------|----------------|------"

  while IFS=$'\t' read -r id company role status last_activity; do
    [[ -z "$id" ]] && continue
    if [[ -n "$filter_status" && "$status" != "$filter_status" ]]; then
      continue
    fi

    local la_epoch days_since
    if ! la_epoch=$(date -j -f "%Y-%m-%d" "$last_activity" +%s 2>/dev/null); then
      echo "WARNING: malformed date '${last_activity}' for ${id} -- showing 0 days" >&2
      la_epoch=$dt_epoch
    fi
    days_since=$(( (dt_epoch - la_epoch) / 86400 ))

    printf "%-8s | %-20s | %-25s | %-14s | %dd\n" "$id" "$company" "$role" "$status" "$days_since"
  done <<< "$app_tsv"
}

cmd_stats() {
  ensure_tracker

  local dow
  if ! dow=$(date +%u 2>&1); then
    echo "ERROR: tracker stats: could not determine day of week: ${dow}" >&2
    return 1
  fi
  local days_since_mon
  days_since_mon=$(( (dow - 1) ))
  local week_start
  if ! week_start=$(date -v-${days_since_mon}d +%Y-%m-%d 2>&1); then
    echo "ERROR: tracker stats: could not compute week start: ${week_start}" >&2
    return 1
  fi

  local total researching applied screening interviewing offer rejected ghosted this_week
  local stats_json
  if ! stats_json=$(yq -o=json '.applications' "$TRACKER" 2>&1); then
    echo "ERROR: tracker stats: yq failed reading tracker: ${stats_json}" >&2
    return 1
  fi
  local stats_line
  if ! stats_line=$(echo "$stats_json" | jq -r --arg ws "$week_start" '[
    length,
    ([.[] | select(.status == "researching")] | length),
    ([.[] | select(.status == "applied")] | length),
    ([.[] | select(.status == "screening")] | length),
    ([.[] | select(.status == "interviewing")] | length),
    ([.[] | select(.status == "offer")] | length),
    ([.[] | select(.status == "rejected")] | length),
    ([.[] | select(.status == "ghosted")] | length),
    ([.[] | select(.date_applied >= $ws)] | length)
  ] | @tsv' 2>&1); then
    echo "ERROR: tracker stats: failed to compute stats: ${stats_line}" >&2
    return 1
  fi
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
  if ! result=$(APPID="$app_id" yq '.applications[] | select(.id == strenv(APPID))' "$TRACKER" 2>&1); then
    echo "ERROR: tracker get: could not read tracker: ${result}" >&2
    exit 1
  fi
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

  # Validate tracker data is a proper array before auditing
  local tracker_raw
  if ! tracker_raw=$(yq -o=json '.applications' "$TRACKER" 2>&1); then
    echo "ERROR: cmd_audit: yq failed to read tracker: ${tracker_raw}" >&2
    exit 1
  fi
  if ! echo "$tracker_raw" | jq -e 'type == "array"' &>/dev/null; then
    echo "ERROR: tracker.yaml .applications is not a valid array"
    echo "Expected an array, got: $(echo "$tracker_raw" | jq -r 'type' 2>/dev/null || echo 'unparseable')"
    echo "Fix the tracker file: ${TRACKER}"
    exit 1
  fi

  # Pre-extract tracker data once (O(1) yq call)
  local tracker_json
  if ! tracker_json=$(echo "$tracker_raw" | jq -r '[.[] | {id: .id, company: .company, company_lower: (.company | ascii_downcase), role: .role, status: .status}]' 2>&1); then
    echo "ERROR: cmd_audit: jq failed to parse tracker data: ${tracker_json}" >&2
    exit 1
  fi

  # Pre-extract CSV active rows using awk for proper quoted-field handling
  # Output: company\trole\tstatus (lowercased company)
  local csv_active
  if ! csv_active=$(awk -F',' '
    NR == 1 { next }
    {
      # Rejoin fields split inside quotes
      line = $0; nf = 0; delete fields
      while (line != "") {
        if (substr(line, 1, 1) == "\"") {
          p = index(substr(line, 2), "\"")
          while (p > 0 && substr(line, p+2, 1) == "\"") {
            p = p + 1 + index(substr(line, p+2+1), "\"")
          }
          fields[++nf] = substr(line, 2, p-1)
          # Unescape doubled quotes ("" -> ") per RFC 4180
          gsub(/""/, "\"", fields[nf])
          line = substr(line, p+3)
        } else {
          p = index(line, ",")
          if (p == 0) { fields[++nf] = line; line = "" }
          else { fields[++nf] = substr(line, 1, p-1); line = substr(line, p+1) }
        }
      }
      company = fields[2]; role = fields[4]; status = fields[11]
      if (status == "applied" || status == "screening" || status == "interviewing" || status == "offer") {
        co = tolower(company)
        print co "\t" company "\t" role "\t" status
      }
    }
  ' "$jobs_csv" 2>&1); then
    echo "ERROR: cmd_audit: awk failed parsing jobs.csv for active rows: ${csv_active}" >&2
    return 1
  fi

  # Pre-build CSV company lookup (lowercased company -> status) for O(1) checks
  local csv_all_companies
  if ! csv_all_companies=$(awk -F',' '
    NR == 1 { next }
    {
      line = $0; nf = 0; delete fields
      while (line != "") {
        if (substr(line, 1, 1) == "\"") {
          p = index(substr(line, 2), "\"")
          while (p > 0 && substr(line, p+2, 1) == "\"") {
            p = p + 1 + index(substr(line, p+2+1), "\"")
          }
          fields[++nf] = substr(line, 2, p-1)
          # Unescape doubled quotes ("" -> ") per RFC 4180
          gsub(/""/, "\"", fields[nf])
          line = substr(line, p+3)
        } else {
          p = index(line, ",")
          if (p == 0) { fields[++nf] = line; line = "" }
          else { fields[++nf] = substr(line, 1, p-1); line = substr(line, p+1) }
        }
      }
      print tolower(fields[2]) "\t" fields[11]
    }
  ' "$jobs_csv" 2>&1); then
    echo "ERROR: cmd_audit: awk failed parsing jobs.csv for company lookup: ${csv_all_companies}" >&2
    return 1
  fi

  # Check applied jobs in jobs.csv that have no tracker entry
  echo "=== Applied jobs.csv rows with no tracker entry ==="
  local csv_issues=0
  while IFS=$'\t' read -r co_lower company role status; do
    [[ -z "$co_lower" ]] && continue
    local found
    if ! found=$(echo "$tracker_json" | jq -r --arg c "$co_lower" '[.[] | . as $obj | select(($obj.company_lower | contains($c)) or ($c | contains($obj.company_lower)))] | length' 2>&1); then
      echo "WARNING: cmd_audit: jq failed for company '${co_lower}' -- skipping" >&2
      continue
    fi
    if [[ "$found" -eq 0 ]]; then
      echo "  MISSING: ${company} / ${role} (jobs.csv status: ${status})"
      csv_issues=$((csv_issues + 1))
      issues=$((issues + 1))
    fi
  done <<< "$csv_active"

  [[ "$csv_issues" -eq 0 ]] && echo "  (none)"

  # Check tracker entries with no jobs.csv row
  echo ""
  echo "=== Tracker entries with no jobs.csv row ==="
  local tracker_issues=0
  local active_tsv
  if ! active_tsv=$(echo "$tracker_json" | jq -r '.[] | select(.status == "applied" or .status == "screening" or .status == "interviewing" or .status == "offer") | [.id, .company, .role, .status] | @tsv' 2>&1); then
    echo "ERROR: could not extract active entries from tracker: ${active_tsv}" >&2
    return 1
  fi

  while IFS=$'\t' read -r id company role status; do
    [[ -z "$id" ]] && continue
    local co_lower
    co_lower=$(echo "$company" | tr '[:upper:]' '[:lower:]')
    local found_in_csv=false

    while IFS=$'\t' read -r csv_co csv_status; do
      [[ -z "$csv_co" ]] && continue
      # Substring match in either direction
      if [[ "$csv_co" == *"$co_lower"* || "$co_lower" == *"$csv_co"* ]]; then
        found_in_csv=true
        break
      fi
    done <<< "$csv_all_companies"

    if [[ "$found_in_csv" == "false" ]]; then
      echo "  MISSING: ${id} | ${company} / ${role} (tracker status: ${status})"
      tracker_issues=$((tracker_issues + 1))
      issues=$((issues + 1))
    fi
  done <<< "$active_tsv"

  [[ "$tracker_issues" -eq 0 ]] && echo "  (none)"

  # Check status mismatches for companies present in both
  echo ""
  echo "=== Status mismatches ==="
  local mismatch_issues=0
  while IFS=$'\t' read -r id company role tracker_status; do
    [[ -z "$id" ]] && continue
    local co_lower
    co_lower=$(echo "$company" | tr '[:upper:]' '[:lower:]')

    while IFS=$'\t' read -r csv_co csv_status; do
      [[ -z "$csv_co" ]] && continue
      if [[ "$csv_co" == *"$co_lower"* || "$co_lower" == *"$csv_co"* ]]; then
        if [[ "$csv_status" != "$tracker_status" ]]; then
          echo "  MISMATCH: ${company} | jobs.csv=${csv_status} tracker=${tracker_status} (${id})"
          mismatch_issues=$((mismatch_issues + 1))
          issues=$((issues + 1))
        fi
        break
      fi
    done <<< "$csv_all_companies"
  done <<< "$active_tsv"

  [[ "$mismatch_issues" -eq 0 ]] && echo "  (none)"

  echo ""
  if [[ "$issues" -eq 0 ]]; then
    echo "OK: tracker and jobs.csv are in sync"
  else
    echo "Found ${issues} discrepancy(s)"
  fi
}

cmd_follow_ups() {
  ensure_tracker

  local dt
  dt=$(today)
  local count
  if ! count=$(yq '.applications | length' "$TRACKER" 2>&1); then
    echo "ERROR: tracker follow-ups: could not read applications from ${TRACKER}: ${count}" >&2
    return 1
  fi

  if [[ "$count" -eq 0 ]]; then
    echo "(no applications tracked yet)"
    return
  fi

  local found=0
  local dt_epoch
  if ! dt_epoch=$(date -j -f "%Y-%m-%d" "$dt" +%s 2>&1); then
    echo "ERROR: tracker follow-ups: invalid date '${dt}': ${dt_epoch}" >&2
    return 1
  fi

  local fu_json
  if ! fu_json=$(yq -o=json '.applications' "$TRACKER" 2>&1); then
    echo "ERROR: tracker follow-ups: yq failed reading tracker: ${fu_json}" >&2
    return 1
  fi
  local fu_tsv
  if ! fu_tsv=$(echo "$fu_json" | jq -r '.[] | select(.status == "applied" or .status == "screening" or .status == "interviewing") | [.id, .company, .role, .status, .last_activity, .follow_up_date] | @tsv' 2>&1); then
    echo "ERROR: tracker follow-ups: could not parse applications from ${TRACKER}" >&2
    return 1
  fi

  [[ -z "$fu_tsv" ]] && { echo "(no active applications with follow-ups)"; return; }

  while IFS=$'\t' read -r id company role status last_activity follow_up; do
    if [[ "$follow_up" == "null" || -z "$follow_up" ]]; then
      continue
    fi

    local fu_epoch
    if ! fu_epoch=$(date -j -f "%Y-%m-%d" "$follow_up" +%s 2>/dev/null); then
      echo "WARNING: skipping ${id} (${company}) -- malformed follow_up_date: '${follow_up}'" >&2
      continue
    fi

    if [[ "$fu_epoch" -le "$dt_epoch" ]]; then
      local la_epoch days_since
      if ! la_epoch=$(date -j -f "%Y-%m-%d" "$last_activity" +%s 2>/dev/null); then
        echo "WARNING: malformed date '${last_activity}' for ${id} -- showing 0 days" >&2
        la_epoch=$dt_epoch
      fi
      days_since=$(( (dt_epoch - la_epoch) / 86400 ))

      local label="due"
      if [[ "$fu_epoch" -lt "$dt_epoch" ]]; then
        label="OVERDUE"
      fi

      printf "%s | %-20s | %-25s | %-10s | %dd since activity | follow-up: %s (%s)\n" \
        "$id" "$company" "$role" "$status" "$days_since" "$follow_up" "$label"
      found=$((found + 1))
    fi
  done <<< "$fu_tsv"

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
