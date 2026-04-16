#!/usr/bin/env bash
set -euo pipefail

# Install or uninstall all daily-driver LaunchAgents.
# Substitutes placeholders in plist templates and registers with launchd.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHD_DIR="${SCRIPT_DIR}/../launchd"
AGENTS_DIR="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

PLISTS=(
  "com.daily-driver.day-start"
  "com.daily-driver.checkin"
  "com.daily-driver.day-end"
  "com.daily-driver.gather-jobs"
)

detect_brew_prefix() {
  if [[ -d /opt/homebrew ]]; then
    echo "/opt/homebrew"
  elif [[ -d /usr/local/Cellar ]]; then
    echo "/usr/local"
  else
    echo "ERROR: Homebrew not found"
    exit 1
  fi
}

CONFIG="${SCRIPT_DIR}/../config.yaml"

# Parse "HH:MM" into HOUR and MINUTE globals. Exits on bad format.
parse_time() {
  local raw="$1"
  if [[ ! "$raw" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
    echo "ERROR: invalid time format '${raw}' (expected HH:MM)" >&2
    exit 1
  fi
  # Strip leading zeros for plist <integer> (09 -> 9)
  HOUR=$((10#${BASH_REMATCH[1]}))
  MINUTE=$((10#${BASH_REMATCH[2]}))
}

# Read a schedule time from config.yaml. Falls back to provided default.
read_schedule_time() {
  local yq_path="$1" default="$2"
  local val
  val=$(yq "${yq_path} // \"${default}\"" "$CONFIG" 2>/dev/null) || val="$default"
  echo "$val"
}

cmd_install() {
  local brew_prefix
  brew_prefix=$(detect_brew_prefix)
  local repo_dir
  repo_dir="$(git -C "${SCRIPT_DIR}" worktree list --porcelain | head -1 | sed 's/^worktree //')"
  if [[ -z "$repo_dir" ]]; then
    echo "ERROR: could not determine repo root via git worktree list" >&2
    exit 1
  fi

  if ! command -v yq &>/dev/null; then
    echo "ERROR: yq not installed (needed to read schedule from config.yaml)" >&2
    exit 1
  fi

  # Read schedule times from config
  local day_start_time day_end_time gather_jobs_time
  day_start_time=$(read_schedule_time '.schedule.day_start' '09:00')
  day_end_time=$(read_schedule_time '.schedule.day_end' '17:00')
  gather_jobs_time=$(read_schedule_time '.schedule.gather_jobs' '07:00')

  # Build checkin interval XML from config array
  local checkin_xml="" checkin_count
  checkin_count=$(yq '.schedule.checkin | length' "$CONFIG" 2>/dev/null) || checkin_count=0
  if [[ "$checkin_count" -eq 0 ]]; then
    # Fallback defaults
    checkin_xml="    <dict>\n      <key>Hour</key>\n      <integer>11</integer>\n      <key>Minute</key>\n      <integer>0</integer>\n    </dict>\n    <dict>\n      <key>Hour</key>\n      <integer>15</integer>\n      <key>Minute</key>\n      <integer>0</integer>\n    </dict>"
  else
    for ((i = 0; i < checkin_count; i++)); do
      local t
      t=$(yq ".schedule.checkin[${i}]" "$CONFIG" 2>/dev/null) || continue
      parse_time "$t"
      [[ -n "$checkin_xml" ]] && checkin_xml="${checkin_xml}\n"
      checkin_xml="${checkin_xml}    <dict>\n      <key>Hour</key>\n      <integer>${HOUR}</integer>\n      <key>Minute</key>\n      <integer>${MINUTE}</integer>\n    </dict>"
    done
  fi

  mkdir -p "$AGENTS_DIR"
  chmod +x "${repo_dir}/scripts/open-session.sh"

  for plist_name in "${PLISTS[@]}"; do
    local template="${LAUNCHD_DIR}/${plist_name}.plist"
    local dest="${AGENTS_DIR}/${plist_name}.plist"

    if [[ ! -f "$template" ]]; then
      echo "ERROR: plist template not found: ${template}"
      exit 1
    fi

    # Parse the schedule time for this plist (checkin uses PLACEHOLDER_CHECKIN_INTERVALS instead)
    case "$plist_name" in
      *day-start)   parse_time "$day_start_time" ;;
      *day-end)     parse_time "$day_end_time" ;;
      *gather-jobs) parse_time "$gather_jobs_time" ;;
      *checkin)     HOUR=0; MINUTE=0 ;;
    esac

    sed -e "s|PLACEHOLDER_HOME|${HOME}|g" \
        -e "s|PLACEHOLDER_BREW|${brew_prefix}|g" \
        -e "s|PLACEHOLDER_REPO|${repo_dir}|g" \
        -e "s|PLACEHOLDER_HOUR|${HOUR}|g" \
        -e "s|PLACEHOLDER_MINUTE|${MINUTE}|g" \
        "$template" > "$dest"

    # Checkin plist needs multi-interval substitution (sed can't do multiline easily)
    if [[ "$plist_name" == *checkin ]]; then
      local tmp_dest="${dest}.tmp"
      awk -v intervals="$(echo -e "$checkin_xml")" '{
        if ($0 ~ /PLACEHOLDER_CHECKIN_INTERVALS/) { print intervals }
        else { print }
      }' "$dest" > "$tmp_dest" && mv "$tmp_dest" "$dest"
    fi

    launchctl bootout "$DOMAIN" "$dest" 2>/dev/null || true
    if ! launchctl bootstrap "$DOMAIN" "$dest" 2>&1; then
      echo "ERROR: launchctl bootstrap failed for ${plist_name} -- check plist syntax at ${dest}" >&2
      exit 1
    fi
    echo "Installed: ${plist_name}"
  done

  echo ""
  echo "Schedule (from config.yaml):"
  echo "  ${gather_jobs_time} -> gather-jobs"
  echo "  ${day_start_time} -> /day-start"
  for ((i = 0; i < checkin_count; i++)); do
    local ct
    ct=$(yq ".schedule.checkin[${i}]" "$CONFIG" 2>/dev/null)
    echo "  ${ct} -> /check-in"
  done
  echo "  ${day_end_time} -> /day-end"
}

cmd_uninstall() {
  for plist_name in "${PLISTS[@]}"; do
    local dest="${AGENTS_DIR}/${plist_name}.plist"
    launchctl bootout "${DOMAIN}/${plist_name}" 2>/dev/null || true
    if [[ -f "$dest" ]]; then
      rm -f "$dest"
      echo "Removed: ${plist_name}"
    fi
  done
}

usage() {
  echo "Usage: $(basename "$0") <install|uninstall>"
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

case "$1" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  *)
    echo "ERROR: unknown command: $1"
    usage
    ;;
esac
