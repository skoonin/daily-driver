#!/usr/bin/env bash
set -euo pipefail

# Collects today's Claude session outcomes for check-in and day-end context.
#
# Primary source: ~/.local/share/daily-driver/session-outcomes.log
#   Written by on-session-end.sh (SessionEnd hook) and on-commit.sh (PostToolUse hook).
#   Provides ground truth: session names, durations, commits made.
#
# Fallback: ~/.claude/history.jsonl
#   User prompts only -- used to show what was discussed when outcomes log has no entry.
#
# Crash detection: on-session-start.sh writes a session_start entry. If the SessionEnd
# hook never fires (terminal closed, crash), the start entry has no matching end --
# reported as an unclosed session so the gap is visible rather than silent.

OUTCOMES_LOG="$HOME/.local/share/daily-driver/session-outcomes.log"
TODAY=$(date +%Y-%m-%d)

echo "=== Claude Sessions: ${TODAY} ==="

if [[ -f "$OUTCOMES_LOG" ]]; then
    # Read today's entries, group sessions with their commits
    todays_entries=$(jq -r --arg today "$TODAY" 'select(.ts | startswith($today))' "$OUTCOMES_LOG" 2>/dev/null)

    if [[ -n "$todays_entries" ]]; then
        # Collect unique session_ids from session_end entries (preserving order)
        session_ids=$(echo "$todays_entries" | jq -r 'select(.type == "session_end") | .session_id' | awk '!seen[$0]++')

        if [[ -n "$session_ids" ]]; then
            while IFS= read -r sid; do
                [[ -z "$sid" ]] && continue

                # Get session metadata
                meta=$(echo "$todays_entries" | jq -r --arg sid "$sid" 'select(.type == "session_end" and .session_id == $sid)')
                session_name=$(echo "$meta" | jq -r '.session_name // "unnamed"')
                project=$(echo "$meta" | jq -r '.project // "unknown"')
                duration=$(echo "$meta" | jq -r '.duration_min // empty')
                ts=$(echo "$meta" | jq -r '.ts // empty' | cut -c12-16)  # HH:MM

                duration_str=""
                [[ -n "$duration" ]] && duration_str=", ${duration}min"

                echo ""
                echo "[${ts}] ${session_name} (${project}${duration_str})"

                # Get commits for this session
                commits=$(echo "$todays_entries" | jq -r --arg sid "$sid" 'select(.type == "commit" and .session_id == $sid) | "  \(.hash) - \(.message)"')
                if [[ -n "$commits" ]]; then
                    echo "  Commits:"
                    echo "$commits"
                else
                    echo "  Commits: none"
                fi
            done <<< "$session_ids"
        fi

        # Show any orphaned commits (session ended before hook fired, or different session)
        orphaned=$(echo "$todays_entries" | jq -r --argjson known_sids "$(echo "$todays_entries" | jq -r 'select(.type == "session_end") | .session_id' | jq -Rs '[split("\n")[] | select(. != "")]')" '
            select(.type == "commit") |
            . as $e |
            if ([$known_sids[] | . == $e.session_id] | any) then empty else "  \(.hash) - \(.message) (\(.repo))" end
        ' 2>/dev/null)
        if [[ -n "$orphaned" ]]; then
            echo ""
            echo "[??:??] Commits without session record:"
            echo "$orphaned"
        fi

        # Detect unclosed sessions: session_start with no matching session_end
        ended_sids=$(echo "$todays_entries" | jq -r 'select(.type == "session_end") | .session_id' | sort -u)
        unclosed=$(echo "$todays_entries" | jq -r 'select(.type == "session_start") | .session_id' | while IFS= read -r sid; do
            [[ -z "$sid" ]] && continue
            if ! echo "$ended_sids" | grep -qxF "$sid"; then
                echo "$sid"
            fi
        done)

        if [[ -n "$unclosed" ]]; then
            echo ""
            echo "=== Unclosed Sessions (possible crash) ==="
            while IFS= read -r sid; do
                [[ -z "$sid" ]] && continue
                start_meta=$(echo "$todays_entries" | jq -r --arg sid "$sid" 'select(.type == "session_start" and .session_id == $sid)')
                start_ts=$(echo "$start_meta" | jq -r '.ts // empty' | head -1 | cut -c12-16)
                start_project=$(echo "$start_meta" | jq -r '.project // "unknown"')
                echo "[${start_ts}] ${start_project} — session ${sid:0:8}... (no end record)"
            done <<< "$unclosed"
        fi
    fi
fi

# Fallback / supplement: history.jsonl for active session prompts not yet in outcomes log
HISTORY="$HOME/.claude/history.jsonl"
if [[ -f "$HISTORY" ]]; then
    TODAY_EPOCH=$(date -j -f "%Y-%m-%d %H:%M:%S" "${TODAY} 00:00:00" +%s 2>/dev/null) || true
    if [[ -n "$TODAY_EPOCH" ]]; then
        TODAY_START="${TODAY_EPOCH}000"
        history_results=$(jq -r --arg start "$TODAY_START" '
            select((.timestamp | tostring) >= $start)
            | "- \(.display[:120]) [project=\(.project | split("/") | last)]"
        ' "$HISTORY" 2>/dev/null | sort -u | head -30) || true

        if [[ -n "$history_results" ]]; then
            echo ""
            echo "=== Activity Timeline (prompts) ==="
            echo "$history_results"
        fi
    fi
fi

echo ""
