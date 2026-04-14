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

OUTPUT_DIR=$(bash "${SCRIPT_DIR}/get-output-dir.sh") || exit 1
TRACKER="${OUTPUT_DIR}/tracker.yaml"
if ! FOLLOW_UP_DAYS=$(yq '.tracker.follow_up_days // 7' "$CONFIG" 2>/dev/null); then
  echo "WARNING: tracker: could not read follow_up_days from config, using 7" >&2
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
  if ! raw=$(yq '.applications[].id' "$TRACKER" 2>/dev/null); then
    echo "ERROR: next_id: could not read tracker IDs" >&2
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
  if ! days=$(STATUS="$status" yq ".tracker.follow_up_by_status.[strenv(STATUS)] // ${FOLLOW_UP_DAYS}" "$CONFIG" 2>/dev/null); then
    echo "WARNING: follow_up_date: yq failed reading config, using ${FOLLOW_UP_DAYS} days" >&2
    days="$FOLLOW_UP_DAYS"
  fi
  if [[ -z "$days" || "$days" == "null" || ! "$days" =~ ^[0-9]+$ ]]; then
    days="$FOLLOW_UP_DAYS"
  fi
  date -v+"${days}"d +%Y-%m-%d
}

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//'
}

create_company_doc() {
  local id="$1" company="$2" role="$3" url="$4" source="$5" dt="$6"
  local company_slug role_slug
  company_slug=$(slugify "$company")
  role_slug=$(slugify "$role")
  local dir="${OUTPUT_DIR}/companies/${company_slug}"
  local file="${dir}/${id}-${role_slug}.md"

  if [[ -f "$file" ]]; then
    return 0
  fi

  mkdir -p "$dir"
  cat > "$file" <<TEMPLATE
# ${company} -- ${role}

**App**: ${id}
**Status**: applied
**Applied**: ${dt}
**URL**: ${url}
**Source**: ${source}
**Comp**: TBD
**Location**: TBD

---

## Company

(Research needed)

## Role

(Details from job posting)

## Draws

(What's appealing)

## Concerns

(What to watch for)

## Open Questions

1. Tech stack
2. Team size and structure
3. Interview process

---

## Timeline

### ${dt} -- Applied

- Source: ${source}
- URL: ${url}
TEMPLATE

  echo "Company doc created: ${file}"
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

  ID="$id" COMPANY="$company" ROLE="$role" URL="$url" DT="$dt" FU="$fu" SOURCE="$source" \
    yq -i '.applications += [{"id": strenv(ID), "company": strenv(COMPANY), "role": strenv(ROLE), "url": strenv(URL), "status": "applied", "date_applied": strenv(DT), "last_activity": strenv(DT), "follow_up_date": strenv(FU), "notes": "", "source": strenv(SOURCE)}]' "$TRACKER" \
    || { echo "ERROR: tracker add: yq failed to append application" >&2; exit 1; }

  create_company_doc "$id" "$company" "$role" "$url" "$source" "$dt"

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
  if ! exists=$(APPID="$app_id" yq '.applications[] | select(.id == strenv(APPID)) | .id' "$TRACKER" 2>/dev/null); then
    echo "ERROR: tracker update: could not read tracker" >&2
    exit 1
  fi
  if [[ -z "$exists" ]]; then
    echo "ERROR: ${app_id} not found"
    exit 1
  fi

  VALUE="$value" APPID="$app_id" FIELD="$field" yq -i '(.applications[] | select(.id == strenv(APPID)))[strenv(FIELD)] = strenv(VALUE)' "$TRACKER" \
    || { echo "ERROR: tracker update: yq failed to set ${field} (yq exit non-zero)" >&2; exit 1; }

  APPID="$app_id" ACTIVITY="$(today)" yq -i '(.applications[] | select(.id == strenv(APPID))).last_activity = strenv(ACTIVITY)' "$TRACKER" \
    || { echo "ERROR: tracker update: yq failed to set last_activity (yq exit non-zero)" >&2; exit 1; }

  # Recalculate follow-up date on status change using status-specific interval
  if [[ "$field" == "status" ]]; then
    local fu
    fu=$(follow_up_date "$value")
    APPID="$app_id" FU="$fu" yq -i '(.applications[] | select(.id == strenv(APPID))).follow_up_date = strenv(FU)' "$TRACKER" \
      || { echo "ERROR: tracker update: yq failed to set follow_up_date (yq exit non-zero)" >&2; exit 1; }
  fi

  echo "Updated ${app_id}: ${field} = ${value}"
}

# Loads all applications from tracker as TSV.
# Outputs: tab-separated id, company, role, status, last_activity, follow_up_date
# Prints error to stderr and returns 1 on failure.
# Prints "(no applications tracked yet)" to stdout and returns 0 if tracker is empty.
_load_applications_tsv() {
  local label="${1:-tracker}"

  local count
  if ! count=$(yq '.applications | length' "$TRACKER" 2>/dev/null); then
    echo "ERROR: ${label}: could not read applications from ${TRACKER}" >&2
    return 1
  fi
  if [[ "$count" -eq 0 ]]; then
    echo "(no applications tracked yet)"
    return 0
  fi

  local apps_json
  if ! apps_json=$(yq -o=json '.applications' "$TRACKER" 2>/dev/null); then
    echo "ERROR: ${label}: yq failed reading tracker" >&2
    return 1
  fi

  local tsv
  if ! tsv=$(echo "$apps_json" | jq -r '.[] | [.id, .company, .role, .status, .last_activity, (.follow_up_date // "")] | @tsv' 2>/dev/null); then
    echo "ERROR: ${label}: could not parse applications from ${TRACKER}" >&2
    return 1
  fi

  echo "$tsv"
}

cmd_list() {
  local filter_status="${1:-}"

  ensure_tracker

  local app_tsv
  app_tsv=$(_load_applications_tsv "tracker list") || return 1

  if [[ "$app_tsv" == "(no applications tracked yet)" ]]; then
    echo "$app_tsv"
    return
  fi

  local dt_epoch
  if ! dt_epoch=$(date -j -f "%Y-%m-%d" "$(today)" +%s 2>/dev/null); then
    echo "ERROR: tracker list: could not compute today's epoch" >&2
    return 1
  fi

  printf "%-8s | %-20s | %-25s | %-14s | %s\n" "ID" "Company" "Role" "Status" "Days"
  printf "%s\n" "---------|----------------------|---------------------------|----------------|------"

  while IFS=$'\t' read -r id company role status last_activity _follow_up; do
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
  if ! dow=$(date +%u 2>/dev/null); then
    echo "ERROR: tracker stats: could not determine day of week" >&2
    return 1
  fi
  local days_since_mon
  days_since_mon=$(( (dow - 1) ))
  local week_start
  if ! week_start=$(date -v-${days_since_mon}d +%Y-%m-%d 2>/dev/null); then
    echo "ERROR: tracker stats: could not compute week start" >&2
    return 1
  fi

  local total researching applied screening interviewing offer rejected ghosted this_week
  local stats_json
  if ! stats_json=$(yq -o=json '.applications' "$TRACKER" 2>/dev/null); then
    echo "ERROR: tracker stats: yq failed reading tracker" >&2
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
  ] | @tsv' 2>/dev/null); then
    echo "ERROR: tracker stats: failed to compute stats" >&2
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
  if ! result=$(APPID="$app_id" yq '.applications[] | select(.id == strenv(APPID))' "$TRACKER" 2>/dev/null); then
    echo "ERROR: tracker get: could not read tracker" >&2
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
  if ! tracker_raw=$(yq -o=json '.applications' "$TRACKER" 2>/dev/null); then
    echo "ERROR: cmd_audit: yq failed to read tracker" >&2
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
  if ! tracker_json=$(echo "$tracker_raw" | jq -r '[.[] | {id: .id, company: .company, company_lower: (.company | ascii_downcase), role: .role, status: .status}]' 2>/dev/null); then
    echo "ERROR: cmd_audit: jq failed to parse tracker data" >&2
    exit 1
  fi

  # Parse jobs.csv once into combined intermediate: co_lower TAB company TAB role TAB status
  local csv_combined
  if ! csv_combined=$(awk -F',' '
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
      print tolower(fields[2]) "\t" fields[2] "\t" fields[4] "\t" fields[1]
    }
  ' "$jobs_csv" 2>/dev/null); then
    echo "ERROR: cmd_audit: awk failed parsing jobs.csv: ${csv_combined}" >&2
    return 1
  fi

  # Derive active rows (applied/screening/interviewing/offer) from combined parse
  local csv_active
  csv_active=$(awk -F'\t' '$4 == "applied" || $4 == "screening" || $4 == "interviewing" || $4 == "offer"' <<< "$csv_combined")

  # Build CSV lookup as a JSON array for exact-match jq join
  local csv_json
  if ! csv_json=$(awk -F'\t' 'BEGIN{printf "["} NR>1{printf ","} {printf "{\"co_lower\":\"%s\",\"company\":\"%s\",\"role\":\"%s\",\"status\":\"%s\"}", $1,$2,$3,$4} END{printf "]"}' <<< "$csv_combined" 2>/dev/null); then
    echo "ERROR: cmd_audit: failed to build CSV JSON for join" >&2
    return 1
  fi

  # Check applied jobs in jobs.csv that have no tracker entry
  echo "=== Applied jobs.csv rows with no tracker entry ==="
  local csv_issues=0
  while IFS=$'\t' read -r co_lower company role status; do
    [[ -z "$co_lower" ]] && continue
    local found
    if ! found=$(echo "$tracker_json" | jq -r --arg c "$co_lower" '[.[] | select(.company_lower == $c)] | length' 2>/dev/null); then
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

  # Use jq to compute tracker-vs-csv mismatches in one exact-match join pass.
  # Produces lines prefixed with MISSING or MISMATCH for section routing below.
  local audit_results
  if ! audit_results=$(echo "$tracker_json" | jq -r --argjson csv "$csv_json" '
    [.[] | select(.status == "applied" or .status == "screening" or .status == "interviewing" or .status == "offer")] as $active |
    ($csv | INDEX(.co_lower)) as $csv_idx |
    $active[] |
    . as $t |
    ($csv_idx[$t.company_lower]) as $csv_row |
    if $csv_row == null then
      "MISSING\t\($t.id)\t\($t.company)\t\($t.role)\t\($t.status)"
    elif $csv_row.status != $t.status then
      "MISMATCH\t\($t.id)\t\($t.company)\t\($t.role)\t\($t.status)\t\($csv_row.status)"
    else empty end
  ' 2>/dev/null); then
    echo "ERROR: cmd_audit: jq join failed" >&2
    return 1
  fi

  local missing_output mismatch_output
  missing_output=$(grep $'^MISSING\t' <<< "$audit_results" || true)
  mismatch_output=$(grep $'^MISMATCH\t' <<< "$audit_results" || true)

  echo ""
  echo "=== Tracker entries with no jobs.csv row ==="
  local tracker_issues=0
  if [[ -n "$missing_output" ]]; then
    while IFS=$'\t' read -r _tag id company role status; do
      echo "  MISSING: ${id} | ${company} / ${role} (tracker status: ${status})"
      tracker_issues=$((tracker_issues + 1))
      issues=$((issues + 1))
    done <<< "$missing_output"
  fi
  [[ "$tracker_issues" -eq 0 ]] && echo "  (none)"

  echo ""
  echo "=== Status mismatches ==="
  local mismatch_issues=0
  if [[ -n "$mismatch_output" ]]; then
    while IFS=$'\t' read -r _tag id company role tracker_status csv_status; do
      echo "  MISMATCH: ${company} | jobs.csv=${csv_status} tracker=${tracker_status} (${id})"
      mismatch_issues=$((mismatch_issues + 1))
      issues=$((issues + 1))
    done <<< "$mismatch_output"
  fi
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

  local fu_tsv
  fu_tsv=$(_load_applications_tsv "tracker follow-ups") || return 1

  if [[ "$fu_tsv" == "(no applications tracked yet)" ]]; then
    echo "$fu_tsv"
    return
  fi

  # Filter to active statuses with follow-up dates
  local active_fu_tsv
  active_fu_tsv=$(awk -F'\t' '$4 == "applied" || $4 == "screening" || $4 == "interviewing"' <<< "$fu_tsv")

  if [[ -z "$active_fu_tsv" ]]; then
    echo "(no active applications with follow-ups)"
    return
  fi

  local dt
  dt=$(today)
  local dt_epoch
  if ! dt_epoch=$(date -j -f "%Y-%m-%d" "$dt" +%s 2>/dev/null); then
    echo "ERROR: tracker follow-ups: could not compute today's epoch" >&2
    return 1
  fi

  local found=0

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
  done <<< "$active_fu_tsv"

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
