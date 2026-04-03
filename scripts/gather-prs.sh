#!/usr/bin/env bash
set -euo pipefail

# Lists open PRs across configured GitHub orgs
# Reads orgs from config.yaml

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

for cmd in gh yq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: ${cmd} not installed"
    exit 1
  fi
done

if ! gh auth status &>/dev/null; then
  echo "ERROR: gh not authenticated. Run: gh auth login"
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: config.yaml not found at ${CONFIG}"
  exit 1
fi

while IFS= read -r org; do
  echo "=== GitHub ${org}: My Open PRs ==="
  gh search prs --author=@me --state=open --owner="${org}" --limit 20 2>/dev/null || echo "(no results or no access)"
  echo ""
  echo "=== GitHub ${org}: PRs Requesting My Review ==="
  gh search prs --review-requested=@me --state=open --owner="${org}" --limit 20 2>/dev/null || echo "(no results or no access)"
  echo ""
done < <(yq '.github_orgs[]' "$CONFIG")
