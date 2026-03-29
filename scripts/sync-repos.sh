#!/usr/bin/env bash
set -euo pipefail

# Sync tracked repos, skipping any with uncommitted changes or conflicts
REPOS=("$HOME/.claude" "$HOME/git/code-workspaces")

for repo in "${REPOS[@]}"; do
  [[ -d "$repo/.git" ]] || continue
  repo_name=$(basename "$repo")

  if ! git -C "$repo" diff --quiet 2>/dev/null || ! git -C "$repo" diff --cached --quiet 2>/dev/null; then
    echo "[SKIP] ${repo_name}: uncommitted changes"
    continue
  fi

  if git -C "$repo" pull --rebase --quiet 2>/dev/null; then
    echo "[OK] ${repo_name}: synced"
  else
    echo "[WARN] ${repo_name}: pull failed, aborting rebase"
    git -C "$repo" rebase --abort 2>/dev/null || true
  fi
done
