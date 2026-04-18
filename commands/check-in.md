---
name: check-in
description: Mid-day check-in - review progress against plan and update status
---

## RULED_OUT Protocol

When you decide during this session that a job candidate is not a fit, emit **exactly one line** in this format:

```
RULED_OUT: {"job_key": "<company>|<role>|<link>", "reason": "<short reason>", "source": "check-in"}
```

- `job_key` is `company|role|link` (use `company|role` if link is missing).
- Immediately after emitting the line, invoke:
  ```bash
  bash scripts/record-ruled-out.sh --line 'RULED_OUT: {"job_key":"...","reason":"...","source":"check-in"}'
  ```
- This persists the decision to `{state_dir}/ruled-out.yaml` so future sessions skip the entry.

---

> If this session was resumed from day-start, planning context is already loaded; skip redundant plan re-reads in steps 1-2.

Mid-day check-in session. Follow these steps in order:

## Pre-session: Snapshot and Delta

Snapshot the tracker (records pre-check-in state for delta tracking):
```bash
bash scripts/snapshot-tracker.sh
```

Display what changed since the last session:
```bash
bash scripts/read-session-delta.sh
```

## 0. Capture Current Time

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d)"
```

Use `current_time` throughout this check-in to determine which plan blocks are past, current, and remaining.

## 1. Initialize State

Ensure state exists for today and read the current plan:
```bash
bash scripts/checkin-state.sh init
```

Read today's plan file frontmatter for plan items:
```bash
bash scripts/read-plan-frontmatter.sh
```

Read current state for prior check-ins:
```bash
bash scripts/checkin-state.sh read
```

## 2. Gather Fresh Data

Load the pre-built pipeline summary (avoids re-reading raw files each session):
```bash
bash scripts/show-pipeline-summary.sh
```

Collect calendar context, recent session activity, and git commits:
```bash
bash scripts/gather-calendar.sh
```
```bash
bash scripts/gather-sessions.sh
```
```bash
bash scripts/gather-git-activity.sh
```

Collect application pipeline status and company docs for today's plan:
```bash
bash scripts/gather-applications.sh
```
```bash
bash scripts/gather-company-docs.sh
```

## 3. Present Check-in Summary

Using the gathered data, present:

1. **Current Time vs Remaining Blocks** - Using `current_time` from step 0, show what was planned for this window, what blocks are done/past, and what remains today
2. **Sessions + Commits Since Last Check-in** - Claude Code sessions and git commits since the last check-in timestamp; use these together to infer what was actually worked on
3. **Prior Check-ins Today** - Summary of earlier check-in results if any

## 4. Interactive Review

For each planned item that was scheduled up to now:
- Ask: "What is the status of app-NNN - Company Role task?" (planned / in-progress / done / blocked)
- If the user reports time spent, compare to the planned time block. If actual > planned * 2, flag as an overrun.
- Note any context switches or unplanned work.

## 5. Application Follow-up Reminders

Check for overdue follow-ups:
```bash
bash scripts/tracker.sh follow-ups
```

For each overdue follow-up, ask: "Follow up on app-NNN - Company | Role? It has been N days since last activity."

If the user followed up, update the tracker:
```bash
bash scripts/tracker.sh update app-NNN follow_up_date YYYY-MM-DD
```

## 6. Record Results

Record the check-in data:
```bash
echo '{"completed_tickets": [], "blocked_tickets": [], "notes": ""}' | bash scripts/checkin-state.sh record-checkin
```

Replace the JSON above with the actual ticket lists and notes from the review. Use the real data from the conversation.

For any overruns identified in step 4, record each one:
```bash
bash scripts/checkin-state.sh flag-overrun TICKET PLANNED_MIN ACTUAL_MIN
```

## 7. Adjust Plan

Ask the user: "Do you want to adjust the remaining plan for today?"

If yes, help reprioritize remaining blocks based on what was learned in the check-in. Do not rewrite the plan file unless the user explicitly asks.
