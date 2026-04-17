#!/usr/bin/env bash
set -euo pipefail

# Returns the session ID for a Claude session with an exact customTitle match.
# Searches the project's sessions directory derived from the repo root.
#
# Usage: SESSION_ID=$(bash scripts/find-session-id.sh <session-name>)
#
# Exits 0 with the UUID on stdout if found.
# Exits 1 silently if not found (caller should fall back).
# Exits 1 with an error message on >&2 if the environment is broken.

SESSION_NAME="${1:-}"
if [[ -z "$SESSION_NAME" ]]; then
  echo "ERROR: $(basename "$0"): session name argument required" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Claude stores sessions under ~/.claude/projects/<path-with-slashes-as-dashes>
# e.g. /Users/foo/git/daily-driver -> -Users-foo-git-daily-driver
PROJECT_KEY=$(echo "$REPO_DIR" | tr '/' '-')
SESSIONS_DIR="${HOME}/.claude/projects/${PROJECT_KEY}"

if [[ ! -d "$SESSIONS_DIR" ]]; then
  # No sessions directory yet for this repo path; fall back silently.
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "ERROR: $(basename "$0"): jq not installed" >&2
  exit 1
fi

# Each JSONL file may contain one or more custom-title records. We want the
# most-recently-modified file whose customTitle matches exactly, since the same
# session name can appear in duplicate entries within one file.
MATCH=$(
  grep -rl '"custom-title"' "$SESSIONS_DIR" 2>/dev/null \
    | xargs grep -lF "\"${SESSION_NAME}\"" 2>/dev/null \
    | xargs ls -t 2>/dev/null \
    | head -1
)

if [[ -z "$MATCH" ]]; then
  exit 1
fi

SESSION_ID=$(jq -r --arg name "$SESSION_NAME" \
  'select(.type == "custom-title" and .customTitle == $name) | .sessionId' \
  "$MATCH" 2>/dev/null | head -1)

if [[ -z "$SESSION_ID" ]]; then
  exit 1
fi

echo "$SESSION_ID"
