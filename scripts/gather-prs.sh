#!/usr/bin/env bash
set -euo pipefail

# Lists open PRs across corescientific and core-hpc GitHub orgs
# Requires: gh (GitHub CLI) authenticated

if ! command -v gh &>/dev/null; then
  echo "ERROR: gh not installed"
  exit 1
fi

if ! gh auth status &>/dev/null 2>&1; then
  echo "ERROR: gh not authenticated. Run: gh auth login"
  exit 1
fi

ORGS=("corescientific" "core-hpc")

for org in "${ORGS[@]}"; do
  echo "=== GitHub ${org}: My Open PRs ==="
  gh search prs --author=@me --state=open --owner="${org}" --limit 20 2>/dev/null || echo "(no results or no access)"
  echo ""
  echo "=== GitHub ${org}: PRs Requesting My Review ==="
  gh search prs --review-requested=@me --state=open --owner="${org}" --limit 20 2>/dev/null || echo "(no results or no access)"
  echo ""
done
