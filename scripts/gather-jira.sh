#!/usr/bin/env bash
set -euo pipefail

# Queries configured Jira instances for assigned tickets
# Reads instances from config.yaml, switches between them via acli auth switch

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

for cmd in acli yq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: ${cmd} not installed"
    exit 1
  fi
done

if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: config.yaml not found at ${CONFIG}"
  exit 1
fi

BASE_JQL="assignee = currentUser() AND status NOT IN (Done, Closed)"

site_count=$(yq '.jira | length' "$CONFIG")
for i in $(seq 0 $((site_count - 1))); do
  site=$(yq ".jira[$i].site" "$CONFIG")
  label=$(yq ".jira[$i].label" "$CONFIG")
  project=$(yq ".jira[$i].project" "$CONFIG")

  JQL="${BASE_JQL}"
  if [[ "$project" != "null" && -n "$project" ]]; then
    JQL="project = ${project} AND ${BASE_JQL}"
  fi
  JQL="${JQL} ORDER BY priority DESC, updated DESC"

  echo "=== Jira: ${label} ==="
  if ! acli jira auth switch --site "$site" &>/dev/null; then
    echo "(skipping ${label}: could not switch to ${site})"
    echo ""
    continue
  fi
  acli jira workitem search \
    --jql "${JQL}" \
    --fields "key,summary,status,priority" \
    --limit 20 2>&1 || echo "(not authenticated for ${site} - run: acli jira auth login --site ${site})"
  echo ""
done
