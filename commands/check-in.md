---
name: check-in
description: Mid-day check-in - review progress against plan and update status
---

Mid-day check-in session. Follow these steps in order:

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
cat ~/.local/share/daily-driver/state.json 2>/dev/null || echo "(no state file)"
```

## 2. Gather Fresh Data

Collect calendar context, recent session activity, and git commits:
```bash
bash scripts/gather-calendar.sh
```
```bash
bash scripts/gather-sessions.sh
```
```bash
bash scripts/gather-git.sh
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
