---
name: check-in
description: Mid-day check-in - review progress against plan and update status
---

> If this session was resumed from day-start, planning context is already loaded; skip redundant plan re-reads in steps 1-2.

Mid-day check-in session. Follow these steps in order.

## 0. Capture Current Time

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d)"
```

Use `current_time` throughout this check-in to determine which plan blocks are past, current, and remaining.

## 1. Read Today's Plan

Read today's plan frontmatter for plan items:

```bash
daily-driver read plan --frontmatter
```

```bash
daily-driver read plan
```

## 2. Gather Fresh Data

Collect calendar context, recent session activity, and git commits:

```bash
daily-driver gather calendar
```

```bash
daily-driver gather sessions
```

```bash
daily-driver gather git
```

## 3. Review Application Pipeline

Check for overdue follow-ups and current pipeline state:

```bash
daily-driver tracker follow-ups --overdue
```

```bash
daily-driver tracker list
```

## 4. Present Check-in Summary

Using the gathered data, present:

1. **Current Time vs Remaining Blocks** — Using `current_time` from step 0, show what was planned for this window, what blocks are done/past, and what remains today
2. **Sessions + Commits Since Last Check-in** — Claude Code sessions and git commits since the session start; use these together to infer what was actually worked on
3. **Pipeline Status** — Active applications, any responses received, follow-ups due

## 5. Interactive Review

For each planned item that was scheduled up to now:
- Ask: "What is the status of app-NNN - Company Role task?" (planned / in-progress / done / blocked)
- If the user reports time spent, compare to the planned time block. If actual > planned * 2, flag as an overrun.
- Note any context switches or unplanned work.

## 6. Application Follow-up Reminders

For each overdue follow-up from step 3, ask: "Follow up on app-NNN - Company | Role? It has been N days since last activity." Present as a compact checklist, not prose.

If the user followed up, update the tracker:

```bash
daily-driver tracker update <ID> --extra follow_up_date=$(date +%Y-%m-%d)
```

For status changes:

```bash
daily-driver tracker update <ID> --status <new-status> --note "<note>"
```

## 7. Adjust Plan

Ask the user: "Do you want to adjust the remaining plan for today?"

If yes, help reprioritize remaining blocks based on what was learned in the check-in. Do not rewrite the plan file unless the user explicitly asks.
