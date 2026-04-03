#!/usr/bin/env bash
set -euo pipefail

# Extracts Jira ticket references from today's sessions and git activity, then fetches their status
# Usage: bash scripts/ticket-status-sweep.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/../config.yaml"

PROJECTS=$(yq '.jira[].project' "$CONFIG" 2>/dev/null | tr '\n' ',')
RFC_PROJECTS=$(yq '.jira[].rfc_projects[]' "$CONFIG" 2>/dev/null | tr '\n' ',')
ALL_PROJECTS=$(echo "${PROJECTS}${RFC_PROJECTS}" | sed 's/,$//')

{
  bash "${SCRIPT_DIR}/gather-git-activity.sh" 2>/dev/null
  bash "${SCRIPT_DIR}/gather-sessions.sh" 2>/dev/null
} | bash "${SCRIPT_DIR}/extract-jira-refs.sh" --projects "$ALL_PROJECTS" | bash "${SCRIPT_DIR}/gather-ticket-status.sh"
