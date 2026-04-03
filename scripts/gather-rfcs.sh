#!/usr/bin/env bash
set -euo pipefail

# Queries RFC projects across configured Jira instances
# Reads rfc_projects list per instance from config.yaml, skips instances without any

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

site_count=$(yq '.jira | length' "$CONFIG")
for i in $(seq 0 $((site_count - 1))); do
  site=$(yq ".jira[$i].site" "$CONFIG")
  label=$(yq ".jira[$i].label" "$CONFIG")

  rfc_count=$(yq ".jira[$i].rfc_projects | length" "$CONFIG")
  if [[ "$rfc_count" == "0" ]]; then
    continue
  fi

  # Build comma-separated project list for JQL IN clause
  rfc_projects=""
  for j in $(seq 0 $((rfc_count - 1))); do
    proj=$(yq ".jira[$i].rfc_projects[$j]" "$CONFIG")
    if [[ -n "$rfc_projects" ]]; then
      rfc_projects="${rfc_projects}, ${proj}"
    else
      rfc_projects="${proj}"
    fi
  done

  echo "=== RFCs: ${label} ==="
  if ! acli jira auth switch --site "$site" &>/dev/null; then
    echo "(skipping ${label}: could not switch to ${site})"
    echo ""
    continue
  fi

  echo "--- Assigned ---"
  acli jira workitem search \
    --jql "project IN (${rfc_projects}) AND assignee = currentUser() AND status NOT IN (Done, Closed, Rejected) ORDER BY updated DESC" \
    --fields "key,summary,status" \
    --limit 10 2>&1 || echo "(no assigned RFCs or query failed)"
  echo ""

  echo "--- Pending Approval ---"
  acli jira workitem search \
    --jql "project IN (${rfc_projects}) AND status = \"Pending Approval\" ORDER BY updated DESC" \
    --fields "key,summary,status" \
    --limit 10 2>&1 || echo "(no pending approvals or query failed)"
  echo ""

  echo "--- Recently Approved ---"
  acli jira workitem search \
    --jql "project IN (${rfc_projects}) AND assignee = currentUser() AND status IN (\"Approved\", \"In Progress\") AND updated >= \"-7d\" ORDER BY updated DESC" \
    --fields "key,summary,status" \
    --limit 10 2>&1 || echo "(no recently approved RFCs or query failed)"
  echo ""
done
