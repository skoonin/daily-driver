#!/usr/bin/env bash
set -euo pipefail

# Sync tracked repos, skipping any with uncommitted changes or conflicts
# Reads repo list from config.yaml

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

for cmd in yq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: ${cmd} not installed"
    exit 1
  fi
done

if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: config.yaml not found at ${CONFIG}"
  exit 1
fi

while IFS= read -r repo; do
  repo="${repo/#\~/$HOME}"
  [[ -d "$repo/.git" ]] || continue
  repo_name=$(basename "$repo")

  if ! git -C "$repo" diff --quiet 2>/dev/null || ! git -C "$repo" diff --cached --quiet 2>/dev/null; then
    echo "[SKIP] ${repo_name}: uncommitted changes"
    continue
  fi

  if git -C "$repo" pull --rebase --quiet; then
    echo "[OK] ${repo_name}: synced"
  else
    echo "[WARN] ${repo_name}: pull failed, aborting rebase"
    git -C "$repo" rebase --abort 2>/dev/null || true
  fi
done < <(yq '.sync_repos[]' "$CONFIG")
