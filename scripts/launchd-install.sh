#!/usr/bin/env bash
set -euo pipefail

# Install or uninstall the daily-driver check-in LaunchAgent
# Substitutes placeholders in the plist template and registers with launchd

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_TEMPLATE="${SCRIPT_DIR}/../launchd/com.daily-driver.checkin.plist"
PLIST_NAME="com.daily-driver.checkin"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
DOMAIN="gui/$(id -u)"

# Detect Homebrew prefix
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
  if [[ ! -f "$PLIST_TEMPLATE" ]]; then
    echo "ERROR: plist template not found at ${PLIST_TEMPLATE}"
    exit 1
  fi

  local brew_prefix
  brew_prefix=$(detect_brew_prefix)

  mkdir -p "$HOME/Library/LaunchAgents"

  local repo_dir
  repo_dir="$(cd "${SCRIPT_DIR}/.." && pwd)"

  sed -e "s|PLACEHOLDER_HOME|${HOME}|g" \
      -e "s|PLACEHOLDER_BREW|${brew_prefix}|g" \
      -e "s|PLACEHOLDER_REPO|${repo_dir}|g" \
      "$PLIST_TEMPLATE" > "$PLIST_DEST"

  # Unload existing agent if present
  launchctl bootout "$DOMAIN" "$PLIST_DEST" 2>/dev/null || true

  launchctl bootstrap "$DOMAIN" "$PLIST_DEST"
  echo "LaunchAgent installed and loaded: ${PLIST_DEST}"
  echo "Homebrew prefix: ${brew_prefix}"
  echo "Check-in notifications will fire at 9am, 11am, 3pm, 5pm"
}

cmd_uninstall() {
  launchctl bootout "${DOMAIN}/${PLIST_NAME}" 2>/dev/null || true

  if [[ -f "$PLIST_DEST" ]]; then
    rm -f "$PLIST_DEST"
    echo "LaunchAgent removed: ${PLIST_DEST}"
  else
    echo "LaunchAgent plist not found at ${PLIST_DEST}"
  fi
}

usage() {
  echo "Usage: $(basename "$0") <install|uninstall>"
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

case "$1" in
  install)
    cmd_install
    ;;
  uninstall)
    cmd_uninstall
    ;;
  *)
    echo "ERROR: unknown command: $1"
    usage
    ;;
esac
