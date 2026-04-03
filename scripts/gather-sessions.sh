#!/usr/bin/env bash
set -euo pipefail

# Collects today's Claude Code session summaries from all projects
# Parses sessions-index.json files for sessions modified today

CLAUDE_DIR="$HOME/.claude/projects"
TODAY=$(date +%Y-%m-%d)

if [[ ! -d "$CLAUDE_DIR" ]]; then
  echo "ERROR: Claude projects directory not found at ${CLAUDE_DIR}"
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "ERROR: jq not installed. Run: brew install jq"
  exit 1
fi

echo "=== Claude Sessions: ${TODAY} ==="

found=0
for index in "$CLAUDE_DIR"/*/sessions-index.json; do
  [[ -f "$index" ]] || continue

  results=$(jq -r --arg today "$TODAY" '
    .entries[]
    | select(.modified | startswith($today))
    | "- [\(.summary // (.firstPrompt[:80] + "..."))] project=\(.projectPath | split("/") | last) branch=\(.gitBranch // "n/a") messages=\(.messageCount)"
  ' "$index" 2>/dev/null) || continue

  if [[ -n "$results" ]]; then
    echo "$results"
    found=1
  fi
done

## Supplement with history.jsonl for active/recent sessions not yet indexed
HISTORY="$HOME/.claude/history.jsonl"
if [[ -f "$HISTORY" ]]; then
  TODAY_EPOCH=$(date -j -f "%Y-%m-%d %H:%M:%S" "${TODAY} 00:00:00" +%s 2>/dev/null) || true
  if [[ -n "$TODAY_EPOCH" ]]; then
    TODAY_START="${TODAY_EPOCH}000"
    history_results=$(jq -r --arg start "$TODAY_START" '
      select((.timestamp | tostring) >= $start)
      | "- \(.display[:100]) [project=\(.project | split("/") | last)]"
    ' "$HISTORY" 2>/dev/null | sort -u | head -30) || true

    if [[ -n "$history_results" ]]; then
      echo ""
      echo "=== Activity Timeline (from history) ==="
      echo "$history_results"
      found=1
    fi
  fi
fi

if [[ "$found" -eq 0 ]]; then
  echo "(no sessions found for today)"
fi
