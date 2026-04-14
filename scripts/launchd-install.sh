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

cmd_install() {
  local brew_prefix
  brew_prefix=$(detect_brew_prefix)
  local repo_dir
  repo_dir="$(git -C "${SCRIPT_DIR}" worktree list --porcelain | head -1 | sed 's/^worktree //')"

  mkdir -p "$AGENTS_DIR"
  chmod +x "${repo_dir}/scripts/open-session.sh"

  for plist_name in "${PLISTS[@]}"; do
    local template="${LAUNCHD_DIR}/${plist_name}.plist"
    local dest="${AGENTS_DIR}/${plist_name}.plist"

    if [[ ! -f "$template" ]]; then
      echo "ERROR: plist template not found: ${template}"
      exit 1
    fi

    sed -e "s|PLACEHOLDER_HOME|${HOME}|g" \
        -e "s|PLACEHOLDER_BREW|${brew_prefix}|g" \
        -e "s|PLACEHOLDER_REPO|${repo_dir}|g" \
        "$template" > "$dest"

    launchctl bootout "$DOMAIN" "$dest" 2>/dev/null || true
    if ! launchctl bootstrap "$DOMAIN" "$dest" 2>&1; then
      echo "ERROR: launchctl bootstrap failed for ${plist_name} -- check plist syntax at ${dest}" >&2
      exit 1
    fi
    echo "Installed: ${plist_name}"
  done

  echo ""
  echo "Schedule:"
  echo "  07:00 -> gather-jobs"
  echo "  09:00 -> /day-start"
  echo "  11:00 -> /check-in"
  echo "  15:00 -> /check-in"
  echo "  17:00 -> /day-end"
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
