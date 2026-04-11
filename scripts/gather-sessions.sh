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
    if ! todays_entries=$(jq -R -r --arg today "$TODAY" 'fromjson? | select(.ts | startswith($today))' "$OUTCOMES_LOG" 2>&1); then
        echo "WARNING: gather-sessions: failed to parse outcomes log -- skipping" >&2
        todays_entries=""
    fi

    if [[ -n "$todays_entries" ]]; then
        # Collect unique session_ids from session_end entries (preserving order)
        if ! jq_sids=$(echo "$todays_entries" | jq -r 'select(.type == "session_end") | .session_id' 2>&1); then
            echo "WARNING: gather-sessions: failed to extract session IDs" >&2
            jq_sids=""
        fi
        session_ids=$(echo "$jq_sids" | awk '!seen[$0]++')

        if [[ -n "$session_ids" ]]; then
            while IFS= read -r sid; do
                [[ -z "$sid" ]] && continue

                # Get session metadata
                if ! meta=$(echo "$todays_entries" | jq -r --arg sid "$sid" 'select(.type == "session_end" and .session_id == $sid)' 2>&1); then
                    echo "WARNING: gather-sessions: failed to parse metadata for session ${sid}" >&2
                    continue
                fi
                if ! session_name=$(echo "$meta" | jq -r '.session_name // "unnamed"' 2>&1); then
                    echo "WARNING: gather-sessions: failed to read session_name for ${sid}: ${session_name}" >&2
                    session_name="unnamed"
                fi
                if ! project=$(echo "$meta" | jq -r '.project // "unknown"' 2>&1); then
                    echo "WARNING: gather-sessions: failed to read project for ${sid}: ${project}" >&2
                    project="unknown"
                fi
                if ! duration=$(echo "$meta" | jq -r '.duration_min // empty' 2>&1); then
                    echo "WARNING: gather-sessions: failed to read duration for ${sid}: ${duration}" >&2
                    duration=""
                fi
                if ! ts_raw=$(echo "$meta" | jq -r '.ts // empty' 2>&1); then
                    echo "WARNING: gather-sessions: failed to read ts for ${sid}: ${ts_raw}" >&2
                    ts_raw=""
                fi
                ts="${ts_raw:11:5}"

                duration_str=""
                [[ -n "$duration" ]] && duration_str=", ${duration}min"

                echo ""
                echo "[${ts}] ${session_name} (${project}${duration_str})"

                # Get commits for this session
                if ! commits=$(echo "$todays_entries" | jq -r --arg sid "$sid" 'select(.type == "commit" and .session_id == $sid) | "  \(.hash) - \(.message)"' 2>&1); then
                    echo "WARNING: gather-sessions: failed to parse commits for session ${sid}" >&2
                    commits=""
                fi
                if [[ -n "$commits" ]]; then
                    echo "  Commits:"
                    echo "$commits"
                else
                    echo "  Commits: none"
                fi
            done <<< "$session_ids"
        fi

        # Show any orphaned commits (session ended before hook fired, or different session)
        if ! known_sids_raw=$(echo "$todays_entries" | jq -r 'select(.type == "session_end") | .session_id' 2>&1); then
            echo "WARNING: gather-sessions: failed to extract known session IDs" >&2
            known_sids_raw=""
        fi
        if ! known_sids_json=$(echo "$known_sids_raw" | jq -Rn '[inputs | select(. != "")]' 2>&1); then
            echo "WARNING: gather-sessions: failed to build session ID JSON" >&2
            known_sids_json="[]"
        fi
        if ! orphaned=$(echo "$todays_entries" | jq -r --argjson known_sids "$known_sids_json" '
            select(.type == "commit") |
            . as $e |
            if ([$known_sids[] | . == $e.session_id] | any) then empty else "  \(.hash) - \(.message) (\(.repo))" end
        ' 2>&1); then
            echo "WARNING: gather-sessions: failed to compute orphaned commits" >&2
            orphaned=""
        fi
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
if [[ -f "$HISTORY" && -r "$HISTORY" ]]; then
    if ! TODAY_EPOCH=$(date -j -f "%Y-%m-%d %H:%M:%S" "${TODAY} 00:00:00" +%s 2>&1); then
        echo "WARNING: could not compute today's epoch (non-BSD date?): ${TODAY_EPOCH}" >&2
        TODAY_EPOCH=""
    fi
    if [[ -n "$TODAY_EPOCH" ]]; then
        TODAY_START="${TODAY_EPOCH}000"
        # Run jq against the file into a temp file so we can detect jq failures directly.
        # Previous approach used `${PIPESTATUS[0]}` after command substitution, which
        # captures only the outer (head -30) exit code and silently ignores jq errors.
        jq_tmp=$(mktemp)
        trap 'rm -f "$jq_tmp"' EXIT
        if jq -R -r --arg start "$TODAY_START" '
            fromjson? | select((.timestamp | tostring) >= $start)
            | "- \(.display[:120]) [project=\(.project // "unknown" | split("/") | last)]"
        ' "$HISTORY" > "$jq_tmp" 2>/dev/null; then
            history_results=$(sort -u "$jq_tmp" | head -30)
        else
            echo "WARNING: gather-sessions: history.jsonl parse error -- skipping activity timeline" >&2
            history_results=""
        fi
        rm -f "$jq_tmp"

        if [[ -n "$history_results" ]]; then
            echo ""
            echo "=== Activity Timeline (prompts) ==="
            echo "$history_results"
        fi
    fi
fi

echo ""
