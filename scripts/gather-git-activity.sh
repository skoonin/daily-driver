#!/usr/bin/env bash
set -euo pipefail

# Collects today's git commits across all repos in ~/git/
TODAY=$(date +%Y-%m-%d)
GIT_DIR="$HOME/git"

echo "=== Git Activity: ${TODAY} ==="

found=0
for repo in "$GIT_DIR"/*/; do
  [[ -d "$repo/.git" ]] || continue
  repo_name=$(basename "$repo")

  commits=$(git -C "$repo" log --oneline --after="${TODAY}T00:00:00" --all 2>/dev/null) || continue

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
