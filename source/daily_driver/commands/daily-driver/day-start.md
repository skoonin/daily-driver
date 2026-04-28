---
name: day-start
description: Morning planning - gather context and plan the day
---

Morning planning session. Follow these steps in order.

## 0. Capture Current Time

Record the current time — all time blocks in the plan start from NOW, not the beginning of the day:

```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d) day_of_week=$(date +%A)"
```

Use `current_time` as the earliest start for any planned work block. Time already past is gone — do not schedule work in time that has already elapsed.

## 1. Gather Context

Read the workspace's context and voice profile:

```bash
daily-driver read context
```

```bash
daily-driver read voice-profile
```

Apply the voice patterns for any written communication drafted during or after this session (cover letters, follow-up emails, recruiter replies).

## 2. Gather Calendar and Activity

Collect today's calendar events, recent Claude Code sessions, and git activity:

```bash
daily-driver gather calendar
```

```bash
daily-driver gather sessions
```

```bash
daily-driver gather git
```

## 3. Review Carry-forward

Read yesterday's notes to surface carry-forward items:

```bash
daily-driver gather notes --since yesterday --until today
```

Extract structured carry-forward items from the notes file. Items labeled `BLOCKED`, `STALE`, or `CARRY-OVER` should be surfaced:

- **BLOCKED**: confirm the blocker still applies. If resolved, remove the blocked flag.
- **STALE** (3+ days): raise directly — "This has been carried N days. Is it still active? Options: schedule it concretely today, defer with a target date, or drop it." Do not silently add it to the plan.
- **CARRY-OVER**: add to the proposed plan in natural priority position. Do not treat them as lower priority than new items just because they carried over.

## 4. Review Application Pipeline

Check the tracker for follow-ups due and active applications:

```bash
daily-driver tracker follow-ups --overdue
```

```bash
daily-driver tracker list
```

## 5. Check Status Dashboard

Get an overview of the workspace state:

```bash
daily-driver status
```

## 6. Personal Tasks

Pre-populate personal items as `plan_items` with `type: personal` from three sources:

1. **Recurring** — items listed under "Recurring Tasks" in context.md (read in step 1). Add each as a plan_item with `type: personal`, `recurring: true`, `time_block: null`. Always present; do not ask about these.
2. **Carried over** — carry_forward items from step 3 with `type: personal`. Include all of them; preserve their text and carry_forward_id.
3. **Ad-hoc** — ask the user: "Anything extra today beyond [list the recurring items]? Include anything time-bound (doctor, errands, walks)."

For each ad-hoc item, extract:
- `text`: description
- `time_block`: "HH:MM-HH:MM" if time-bound, null if open-ended
- `recurring: false`

If the user mentions a time but no duration, ask: "How long for that?" (needed for calendar sync).

If no extra tasks, proceed.

## 7. Plan the Day

Using the work-planner agent behavior, present:

1. **Today's Schedule** — Meetings and fixed commitments with time blocks
2. **Application Pipeline** — Follow-ups due, active applications by stage, new leads to research
3. **Carry-forward** — Structured items from step 3 (BLOCKED, STALE, CARRY-OVER) — includes both work and personal items
4. **Proposed Plan** — Time-blocked plan starting from `current_time` (step 0), accounting for meetings, personal items, and buffer. Do not schedule work in time that has already passed. All items (work and personal) are plan_items.

Use the required time block format: `- HH:MM - HH:MM | app-NNN - Company Role - Task description`

Then ask the user:
- Does this look right?
- Anything to add or reprioritize?
- Any known interrupts or blockers today?

## 8. Save Plan

After the user confirms, ensure today's daily directory exists:

```bash
daily-driver ensure-daily-dir
```

Write the plan to the path printed by `ensure-daily-dir` (e.g., `{output_dir}/YYYY/MM/YYYY-MM-DD-plan.md`) with YAML frontmatter followed by the markdown body. The frontmatter must include:

- `date`: today's date
- `generated_at`: current time HH:MM
- `carry_forward`: items from step 3 (`carried_days` already incremented — copy values as-is). Includes both work and personal items.
- `plan_items`: one entry per planned item (work and personal) with `time_block`, `app_id`, `type`, `status: planned`, `carry_forward_id` linking to source cf-NNN where applicable, and for personal items: `recurring`

See the work-planner agent's "Plan File Frontmatter" section for the exact schema.
