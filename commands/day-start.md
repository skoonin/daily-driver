---
name: day-start
description: Morning planning - gather context and plan the day
---

Morning planning session. Follow these steps in order:

## 0. Capture Current Time

Record the current time -- all time blocks in the plan start from NOW, not the beginning of the day:
```bash
echo "current_time=$(date +%H:%M) current_date=$(date +%Y-%m-%d) day_of_week=$(date +%A)"
```

Use `current_time` as the earliest start for any planned work block. Time already past is gone -- do not schedule work in time that has already elapsed.

## 1. Sync Repos

Run the sync script silently:
```bash
bash scripts/sync-repos.sh
```

## 2. Gather Data

Run all gather scripts and collect their output:
```bash
bash scripts/gather-calendar.sh
```
```bash
bash scripts/gather-applications.sh
```

## 3. Check Carry-forward

Extract structured carry-forward items from yesterday's notes:
```bash
bash scripts/gather-carryforward.sh
```

## 4. Read Context

Read the user's work context:
```bash
bash scripts/read-context.sh
```

## 5. Personal Tasks

Pre-populate personal items as `plan_items` with `type: personal` from three sources:
1. **Recurring** — items listed under "Recurring Tasks" in `context.md`. Add each as a plan_item with `type: personal`, `recurring: true`, `time_block: null`. Always present; do not ask about these.
2. **Carried over** — carry_forward items from step 3 with `type: personal`. Include all of them; preserve their text and carry_forward_id.
3. **Ad-hoc** — ask the user: "Anything extra today beyond [list the recurring items]? Include anything time-bound (doctor, errands, walks)."

For each ad-hoc item, extract:
- `text`: description
- `time_block`: "HH:MM-HH:MM" if time-bound, null if open-ended
- `recurring: false`

If the user mentions a time but no duration, ask: "How long for that?" (needed for calendar sync).

If no extra tasks, proceed.

## 6. Plan the Day

Using the work-planner agent behavior, present:

1. **Today's Schedule** - Meetings and fixed commitments with time blocks
2. **Application Pipeline** - Follow-ups due, active applications by stage, new leads to research
3. **Carry-forward** - Structured items from yesterday (BLOCKED, STALE, CARRY-OVER) -- includes both work and personal items
4. **Proposed Plan** - Time-blocked plan starting from `current_time` (step 0), accounting for meetings, personal items, and buffer. Do not schedule work in time that has already passed. All items (work and personal) are plan_items.

Use the required time block format: `- HH:MM - HH:MM | app-NNN - Company Role - Task description`

Then ask the user:
- Does this look right?
- Anything to add or reprioritize?
- Any known interrupts or blockers today?

## 7. Save Plan

After the user confirms, save the plan to the daily-notes output directory:

```bash
bash scripts/ensure-daily-dir.sh
```

Write the plan to `{output_dir}/YYYY/MM/YYYY-MM-DD-plan.md` with YAML frontmatter followed by the markdown body. The frontmatter must include:
- `date`: today's date
- `generated_at`: current time HH:MM
- `carry_forward`: items from gather-carryforward.sh output (`carried_days` already incremented by the script -- copy values as-is). Includes both work and personal items.
- `plan_items`: one entry per planned item (work and personal) with `time_block`, `app_id`, `type`, `status: planned`, `carry_forward_id` linking to source cf-NNN where applicable, and for personal items: `recurring`

See the work-planner agent's "Plan File Frontmatter" section for the exact schema.

## 8. Sync to Calendar

Sync the confirmed plan's time blocks to macOS Calendar:
```bash
bash scripts/calendar-sync.sh
```

If the script reports "calendar sync disabled" or "no time blocks found", continue without error. This step is optional and non-blocking.
