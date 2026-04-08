#!/usr/bin/env bash
set -euo pipefail

# Manages focus mode via a lock file
# Lock stored at ~/.local/share/daily-driver/focus.lock

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

for cmd in jq yq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: ${cmd} not installed. Run: brew install ${cmd}"
    exit 1
  fi
done

STATE_DIR="$HOME/.local/share/daily-driver"
LOCK_FILE="${STATE_DIR}/focus.lock"

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
}

cmd_enable() {
  local minutes="${1:-}"
  local ticket="${2:-}"

  if [[ -z "$minutes" ]]; then
    if [[ -f "$CONFIG" ]]; then
      minutes=$(yq '.checkin.default_focus_minutes // 90' "$CONFIG")
    else
      minutes=90
    fi
  fi

  ensure_state_dir

  local now_epoch
  now_epoch=$(date +%s)
  local end_epoch=$((now_epoch + minutes * 60))
  local start_iso
  start_iso=$(date -u +%Y-%m-%dT%H:%M:%S)
  local end_iso
  end_iso=$(date -u -r "$end_epoch" +%Y-%m-%dT%H:%M:%S)
  local end_local
  end_local=$(date -r "$end_epoch" +%H:%M)

  local lock_json
  lock_json=$(jq -n --argjson end_epoch "$end_epoch" \
    --arg start_iso "$start_iso" --arg end_iso "$end_iso" \
    --arg ticket "${ticket:-}" \
    '{enabled: true, start_iso: $start_iso, end_iso: $end_iso, end_epoch: $end_epoch, ticket: (if $ticket == "" then null else $ticket end)}')

  printf '%s\n' "$lock_json" > "$LOCK_FILE"

  echo "Focus mode enabled until ${end_local} (${minutes} minutes)"
}

_cleanup_focus() {
  if [[ ! -f "$LOCK_FILE" ]]; then
    return 1
  fi

  local start_iso ticket
  start_iso=$(jq -r '.start_iso // ""' "$LOCK_FILE")
  ticket=$(jq -r '.ticket // ""' "$LOCK_FILE")

  if [[ -n "$start_iso" ]]; then
    local actual_end_iso session_json
    actual_end_iso=$(date -u +%Y-%m-%dT%H:%M:%S)
    session_json=$(jq -n --arg start "$start_iso" --arg end "$actual_end_iso" \
      --arg ticket "${ticket:-}" \
      '{start_iso: $start, end_iso: $end, ticket: (if $ticket == "" or $ticket == "null" then null else $ticket end)}')
    echo "$session_json" | bash "${SCRIPT_DIR}/checkin-state.sh" record-focus-session || true
  fi

  rm -f "$LOCK_FILE"
}

cmd_disable() {
  if ! _cleanup_focus; then
    echo "Focus mode is not active"
    return
  fi

  bash "${SCRIPT_DIR}/open-session.sh" check-in
  echo "Focus mode disabled"
}

cmd_status() {
  if [[ ! -f "$LOCK_FILE" ]]; then
    echo "not in focus mode"
    return
  fi

  local end_epoch now_epoch
  end_epoch=$(jq -r '.end_epoch // 0' "$LOCK_FILE")
  now_epoch=$(date +%s)

  if [[ "$now_epoch" -ge "$end_epoch" ]]; then
    _cleanup_focus
    echo "Focus mode expired"
    return
  fi

  local remaining=$(( (end_epoch - now_epoch) / 60 ))
  local ticket
  ticket=$(jq -r '.ticket // ""' "$LOCK_FILE")
  local end_local
  end_local=$(date -r "$end_epoch" +%H:%M)

  echo "=== Focus Mode: Active ==="
  echo "Until: ${end_local} (${remaining}m remaining)"
  if [[ -n "$ticket" && "$ticket" != "null" ]]; then
    echo "Ticket: ${ticket}"
  fi
}

usage() {
  echo "Usage: $(basename "$0") <command> [args]"
  echo ""
  echo "Commands:"
  echo "  enable [MINUTES] [TICKET]   Enable focus mode"
  echo "  disable                     Disable focus mode"
  echo "  status                      Show focus mode status"
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

case "$1" in
  enable)
    cmd_enable "${2:-}" "${3:-}"
    ;;
  disable)
    cmd_disable
    ;;
  status)
    cmd_status
    ;;
  *)
    echo "ERROR: unknown command: $1"
    usage
    ;;
esac
