#!/usr/bin/env bash
set -euo pipefail

# Collects today's git commits across all repos in the configured scan directory
# Reads scan dir from config.yaml

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

TODAY=$(date +%Y-%m-%d)
GIT_DIR=$(yq '.git_scan_dir' "$CONFIG")
GIT_DIR="${GIT_DIR/#\~/$HOME}"

echo "=== Git Activity: ${TODAY} ==="

found=0
for repo in "$GIT_DIR"/*/; do
  [[ -d "$repo/.git" ]] || continue
  repo_name=$(basename "$repo")

  author=$(git -C "$repo" config user.email 2>/dev/null || echo "")
  commits=$(git -C "$repo" log --oneline --after="${TODAY}T00:00:00" --all ${author:+--author="$author"} 2>/dev/null) || continue

  if [[ -n "$commits" ]]; then
    echo "### ${repo_name}"
    echo "$commits"
    echo ""
    found=1
  fi
done

if [[ "$found" -eq 0 ]]; then
  echo "(no commits found for today)"
fi
