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
if ! GIT_DIR=$(yq '.git_scan_dir' "$CONFIG" 2>&1); then
  echo "ERROR: gather-git-activity: yq failed reading git_scan_dir: ${GIT_DIR}" >&2
  exit 1
fi
if [[ "$GIT_DIR" == "null" || -z "$GIT_DIR" ]]; then
  echo "ERROR: git_scan_dir not set in config.yaml"
  exit 1
fi
GIT_DIR="${GIT_DIR/#\~/$HOME}"

if [[ ! -d "$GIT_DIR" ]]; then
  echo "ERROR: git_scan_dir does not exist or is not a directory: ${GIT_DIR}"
  exit 1
fi

echo "=== Git Activity: ${TODAY} ==="

found=0
# find discovers repos at arbitrary depth; top-level-only glob misses worktrees and subdirectories
while IFS= read -r git_dir; do
  repo="${git_dir%/.git}"
  repo_name="${repo#"$GIT_DIR"/}"

  if ! author=$(git -C "$repo" config user.email 2>/dev/null); then
    author=""
  fi
  commits=$(git -C "$repo" log --oneline --after="${TODAY}T00:00:00" --all ${author:+--author="$author"} 2>/dev/null) || continue

  if [[ -n "$commits" ]]; then
    echo "### ${repo_name}"
    echo "$commits"
    echo ""
    found=1
  fi
done < <(find "$GIT_DIR" -maxdepth 3 -name .git \( -type d -o -type f \) 2>/dev/null)

if [[ "$found" -eq 0 ]]; then
  echo "(no commits found for today)"
fi
