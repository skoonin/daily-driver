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
bash scripts/gather-jira.sh
```
```bash
bash scripts/gather-prs.sh
```
```bash
bash scripts/gather-rfcs.sh
```

## 2b. Read Standup Time

```bash
yq '.reporting.standup.time // "11:00"' config.yaml
```

Treat the standup time as a fixed daily commitment (like a meeting). Block 15 minutes for it in the plan. If the standup time has already passed (compare to `current_time` from step 0), skip blocking it.

## 3. Check Carry-forward

Extract structured carry-forward items from yesterday's notes:
```bash
bash scripts/gather-carryforward.sh
```

## 4. Read Context

Read the user's work context:
```bash
cat context.md
```

## 5. Personal Tasks

Ask the user: "Any personal tasks or appointments today? Include anything time-bound (doctor, errands, walks)."

Accept free-form input. For each item, extract:
- `text`: description
- `time`: HH:MM in 24h if mentioned (e.g., "at 2pm" -> "14:00"), null if open-ended
- `duration_minutes`: if a range is given (e.g., "3-4pm" -> 60), null if unknown
- `notes`: any extra context

If the user mentions a time but no duration, ask: "How long for that?" (needed for calendar sync).

If no personal tasks, proceed.

## 6. Plan the Day

Using the work-planner agent behavior, present:

1. **Today's Schedule** - Meetings and fixed commitments with time blocks
2. **Jira Tickets** - Assigned/in-progress from each configured instance, sorted by priority
3. **RFCs** - Pending approval, recently approved, and assigned RFCs
4. **PR Activity** - Your open PRs and review requests, across configured orgs
5. **Carry-forward** - Structured items from yesterday (BLOCKED, STALE, CARRY-OVER)
6. **Personal Tasks** - Time-bound and open-ended personal items
7. **Proposed Plan** - Time-blocked plan starting from `current_time` (step 0), accounting for meetings, personal tasks, and buffer. Do not schedule work in time that has already passed.

Use the required time block format: `- HH:MM - HH:MM | TICKET-123 - Task summary description`

Then ask the user:
- Does this look right?
- Anything to add or reprioritize?
- Any known interrupts or blockers today?
- Which PRs (if any) do you want to review today? (present the review request list with numbers)

## 6b. Launch PR Reviews

For each PR the user selects for review, open a new iTerm2 window with claude in review mode:

```bash
bash scripts/open-iterm-window.sh --dir ~/git/reviews "claude --model sonnet --effort high '/sk-review PR_URL'"
```

Replace `PR_URL` with the actual GitHub PR URL. Each review runs in its own session.

If no PRs selected, skip this step.

## 7. Save Plan

After the user confirms, save the plan to the daily-notes output directory:

```bash
bash scripts/ensure-daily-dir.sh
```

Write the plan to `{output_dir}/YYYY/MM/YYYY-MM-DD-plan.md` with YAML frontmatter followed by the markdown body. The frontmatter must include:
- `date`: today's date
- `generated_at`: current time HH:MM
- `carry_forward`: items from gather-carryforward.sh output, with `carried_days` incremented by 1
- `personal_tasks`: items collected in step 5
- `plan_items`: one entry per planned item with `time_block`, `jira`, `type`, `status: planned`, and `carry_forward_id` linking to source cf-NNN where applicable

See the work-planner agent's "Plan File Frontmatter" section for the exact schema.

## 8. Sync to Calendar

Sync the confirmed plan's time blocks to macOS Calendar:
```bash
bash scripts/calendar-sync.sh
```

If the script reports "calendar sync disabled" or "no time blocks found", continue without error. This step is optional and non-blocking.
