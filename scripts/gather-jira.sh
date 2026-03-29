#!/usr/bin/env bash
set -euo pipefail

# Queries core-hpc Jira instance for assigned tickets
# Setup: acli jira auth login --site core-hpc.atlassian.net --email <email> --web

if ! command -v acli &>/dev/null; then
  echo "ERROR: acli not installed"
  exit 1
fi

JQL="assignee = currentUser() AND status NOT IN (Done, Closed) ORDER BY priority DESC, updated DESC"

echo "=== Jira: core-hpc (SRE) ==="
acli jira workitem search \
  --jql "${JQL}" \
  --fields "key,summary,status,priority" \
  --limit 20 2>&1 || echo "(not authenticated - run: acli jira auth login --site core-hpc.atlassian.net --email <email> --web)"
