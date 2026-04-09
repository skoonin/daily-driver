#!/usr/bin/env bash
set -euo pipefail

# Manages runtime state for check-in tracking
# State stored at ~/.local/share/daily-driver/state.json

for cmd in jq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: ${cmd} not installed. Run: brew install ${cmd}"
    exit 1
  fi
done

STATE_DIR="$HOME/.local/share/daily-driver"
STATE_FILE="${STATE_DIR}/state.json"
TODAY=$(date +%Y-%m-%d)

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
}

# Archive state from a previous date and create fresh state for today
archive_and_reset() {
  if [[ -f "$STATE_FILE" ]]; then
    local old_date
    old_date=$(jq -r '.date // ""' "$STATE_FILE" 2>/dev/null) || old_date=""
    if [[ -n "$old_date" && "$old_date" != "$TODAY" ]]; then
      mv "$STATE_FILE" "${STATE_DIR}/${old_date}-state.json"
    fi
  fi
  jq -n --arg date "$TODAY" '{date: $date, checkins: [], overruns: [], focus_sessions: []}' > "$STATE_FILE"
}

cmd_init() {
  ensure_state_dir
  if [[ ! -f "$STATE_FILE" ]]; then
    archive_and_reset
    return
  fi
  local current_date
  current_date=$(jq -r '.date // ""' "$STATE_FILE" 2>/dev/null) || current_date=""
  if [[ "$current_date" != "$TODAY" ]]; then
    archive_and_reset
  fi
}

cmd_record_checkin() {
  cmd_init
  local input
  input=$(cat)
  local timestamp
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%S)
  local updated
  updated=$(jq --arg ts "$timestamp" --argjson data "$input" \
    '.checkins += [$data + {timestamp: $ts}]' "$STATE_FILE")
  printf '%s\n' "$updated" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

cmd_get_last_checkin() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "none"
    return
  fi
  local last
  last=$(jq -r '.checkins[-1].timestamp // "none"' "$STATE_FILE" 2>/dev/null) || last="none"
  echo "$last"
}

cmd_flag_overrun() {
  local ticket="$1" planned_min="$2" actual_min="$3"
  cmd_init
  local timestamp
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%S)
  local updated
  updated=$(jq --arg ticket "$ticket" --argjson planned "$planned_min" \
    --argjson actual "$actual_min" --arg ts "$timestamp" \
    '.overruns += [{ticket: $ticket, planned_minutes: $planned, actual_minutes: $actual, timestamp: $ts}]' "$STATE_FILE")
  printf '%s\n' "$updated" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

cmd_record_focus_session() {
  cmd_init
  local input
  input=$(cat)
  local updated
  updated=$(jq --argjson session "$input" \
    '.focus_sessions += [$session]' "$STATE_FILE")
  printf '%s\n' "$updated" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

cmd_read() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "(no check-in state for today)"
    return
  fi
  local file_date
  file_date=$(jq -r '.date // ""' "$STATE_FILE" 2>/dev/null) || file_date=""
  if [[ "$file_date" != "$TODAY" ]]; then
    echo "(no check-in state for today)"
    return
  fi
  jq '.' "$STATE_FILE"
}

usage() {
  echo "Usage: $(basename "$0") <command> [args]"
  echo ""
  echo "Commands:"
  echo "  init                                  Initialize state for today"
  echo "  record-checkin                        Record check-in (reads JSON from stdin)"
  echo "  get-last-checkin                      Print last check-in timestamp"
  echo "  flag-overrun TICKET PLANNED ACTUAL    Record a time overrun"
  echo "  record-focus-session                  Record focus session (reads JSON from stdin)"
  echo "  read                                  Print today's state.json (pretty-printed)"
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

case "$1" in
  init)
    cmd_init
    ;;
  record-checkin)
    cmd_record_checkin
    ;;
  get-last-checkin)
    cmd_get_last_checkin
    ;;
  flag-overrun)
    if [[ $# -lt 4 ]]; then
      echo "ERROR: flag-overrun requires TICKET PLANNED_MIN ACTUAL_MIN"
      exit 1
    fi
    cmd_flag_overrun "$2" "$3" "$4"
    ;;
  record-focus-session)
    cmd_record_focus_session
    ;;
  read)
    cmd_read
    ;;
  *)
    echo "ERROR: unknown command: $1"
    usage
    ;;
esac
