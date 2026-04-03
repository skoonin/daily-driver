#!/usr/bin/env bash
set -euo pipefail

# Extracts Jira ticket references from text on stdin
# Usage: echo "Fixed SRE-142 and IM-88" | scripts/extract-jira-refs.sh [--projects "SRE,IM,HPCRFC"]

PROJECTS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --projects)
      PROJECTS="$2"
      shift 2
      ;;
    *)
      echo "ERROR: unknown option: $1"
      echo "Usage: ... | extract-jira-refs.sh [--projects \"SRE,IM,HPCRFC\"]"
      exit 1
      ;;
  esac
done

if [[ -n "$PROJECTS" ]]; then
  # Build alternation from comma-separated list: SRE,IM -> (SRE|IM)
  pattern="(${PROJECTS//,/|})-[0-9]+"
else
  pattern="[A-Z]{2,10}-[0-9]+"
fi

grep -Eo "$pattern" | sort -u || true
