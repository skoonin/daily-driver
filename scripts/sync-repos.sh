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

if ! sync_repos_raw=$(yq '.sync_repos[]' "$CONFIG" 2>&1); then
  echo "ERROR: could not read sync_repos from config.yaml: ${sync_repos_raw}" >&2
  exit 1
fi
if [[ -z "$sync_repos_raw" || "$sync_repos_raw" == "null" ]]; then
  echo "WARNING: sync_repos is empty or not set in config.yaml" >&2
  exit 0
fi

while IFS= read -r repo; do
  repo="${repo/#\~/$HOME}"
  if [[ ! -d "$repo/.git" ]]; then
    echo "WARNING: ${repo}: not a git repository -- skipping" >&2
    continue
  fi
  repo_name=$(basename "$repo")

  if ! git -C "$repo" diff --quiet 2>/dev/null || ! git -C "$repo" diff --cached --quiet 2>/dev/null; then
    echo "[SKIP] ${repo_name}: uncommitted changes"
    continue
  fi

  if git -C "$repo" pull --rebase --quiet; then
    echo "[OK] ${repo_name}: synced"
  else
    echo "[WARN] ${repo_name}: pull failed, aborting rebase"
    if ! git -C "$repo" rebase --abort 2>&1; then
      echo "[WARN] ${repo_name}: rebase --abort also failed — repo may need manual intervention"
    fi
  fi
done <<< "$sync_repos_raw"
