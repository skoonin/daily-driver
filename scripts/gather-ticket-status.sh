#!/usr/bin/env bash
set -euo pipefail

# Queries current Jira status for ticket keys provided on stdin
# Usage: echo -e "SRE-142\nIM-88" | scripts/gather-ticket-status.sh

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

# Read all ticket keys from stdin
tickets=()
while IFS= read -r line; do
  line="${line// /}"
  if [[ -n "$line" ]]; then
    tickets+=("$line")
  fi
done

if [[ ${#tickets[@]} -eq 0 ]]; then
  exit 0
fi

echo "=== Ticket Status Sweep ==="

site_count=$(yq '.jira | length' "$CONFIG")

# Process tickets per instance without associative arrays (Bash 3.2 compatible)
for i in $(seq 0 $((site_count - 1))); do
  pattern=$(yq ".jira[$i].ticket_pattern" "$CONFIG")
  if [[ "$pattern" == "null" || -z "$pattern" ]]; then
    continue
  fi

  site=$(yq ".jira[$i].site" "$CONFIG")
  label=$(yq ".jira[$i].label" "$CONFIG")

  # Collect tickets matching this instance's pattern
  key_list=""
  match_count=0
  for ticket in "${tickets[@]}"; do
    if echo "$ticket" | grep -Eq "^${pattern}$"; then
      if [[ -n "$key_list" ]]; then
        key_list="${key_list}, ${ticket}"
      else
        key_list="${ticket}"
      fi
      match_count=$((match_count + 1))
    fi
  done

  if [[ "$match_count" -eq 0 ]]; then
    continue
  fi

  if ! acli jira auth switch --site "$site" &>/dev/null; then
    for ticket in "${tickets[@]}"; do
      if echo "$ticket" | grep -Eq "^${pattern}$"; then
        echo "${ticket} [AUTH FAILED] (could not switch to ${site})"
      fi
    done
    continue
  fi

  acli jira workitem search \
    --jql "key IN (${key_list}) ORDER BY key ASC" \
    --fields "key,summary,status" \
    --limit 50 2>&1 || echo "(query failed for ${label})"
done

# Report tickets that matched no instance
for ticket in "${tickets[@]}"; do
  matched=false
  for i in $(seq 0 $((site_count - 1))); do
    pattern=$(yq ".jira[$i].ticket_pattern" "$CONFIG")
    if [[ "$pattern" != "null" && -n "$pattern" ]]; then
      if echo "$ticket" | grep -Eq "^${pattern}$"; then
        matched=true
        break
      fi
    fi
  done
  if [[ "$matched" == "false" ]]; then
    echo "${ticket} [UNKNOWN] (no matching Jira instance)"
  fi
done
