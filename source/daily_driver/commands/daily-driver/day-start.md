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

Also read today's daily-state to detect a late start:

```bash
daily-driver paths daily-state
```

Read the YAML at that path. If the file is missing or `late_day` is unset, treat it as `false` and continue. If `late_day: true`, frame the agenda as a late-day partial plan: prioritize what's still actionable in the remaining hours, defer non-urgent items to tomorrow, and flag the late start at the top of the rendered plan ("Late-day start at HH:MM — partial plan only"). Do NOT block the flow on late_day; it is informational metadata only.

## 1. Gather Context

Resolve the workspace output directory, then Read `context.md` and `voice-profile.md` from it using the Claude Read tool:

```bash
daily-driver paths output
```

Read `<output>/context.md` and `<output>/voice-profile.md`. If either file is missing, note it and continue — they are optional. Apply the voice patterns for any written communication drafted during or after this session (cover letters, follow-up emails, recruiter replies).

## 2. Gather Calendar and Activity

Collect today's calendar events, recent Claude Code sessions, and git activity:

```bash
daily-driver gather calendar
```

For Claude Code sessions, list recent JSONL files directly (avoid context-heavy CLI scans):

```bash
ls -t ~/.claude/projects/*/*.jsonl 2>/dev/null | head -20
```

Read the most recent few that match today's window. Skim for cwd + first user prompt to identify what was worked on.

```bash
daily-driver gather git
```

## 3. Review Carry-forward

Resolve yesterday's notes path and Read it:

```bash
daily-driver paths daily-notes --date $(date -v-1d +%Y-%m-%d)
```

Read the file at the printed path with the Claude Read tool. If it does not exist, there is no carry-forward — proceed.

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

Use the required time block format: `- HH:MM - HH:MM | <tracker-id> - <label> - Task description` where `<tracker-id>` is the `{category}-NNN` ID from the tracker (e.g. `task-012`, `job-003`). For items not tied to a tracker entry (personal, ad-hoc), omit the tracker-id segment.

Then ask the user:
- Does this look right?
- Anything to add or reprioritize?
- Any known interrupts or blockers today?

## 8. Save Plan

`daily-driver day-start` has already written a plan stub at the canonical path
and recorded the day's state in `<workspace>/.daily-driver/state/daily/<date>.yaml`
— do NOT mint a new file. Resolve the canonical path and overwrite the stub
with the finalized plan:

```bash
daily-driver paths daily-plan
```

Write the plan to that path with YAML frontmatter followed by the markdown body. The frontmatter must include:

- `date`: today's date
- `generated_at`: current time HH:MM
- `carry_forward`: items from step 3 (`carried_days` already incremented — copy values as-is). Includes both work and personal items.
- `plan_items`: one entry per planned item (work and personal) with `time_block`, `tracker_id` (the `{category}-NNN` ID, or `null` for items not tied to a tracker entry), `type`, `status: planned`, `carry_forward_id` linking to source cf-NNN where applicable, and for personal items: `recurring`

See the work-planner agent's "Plan File Frontmatter" section for the exact schema.
