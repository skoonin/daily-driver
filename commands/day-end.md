---
name: day-end
description: End of day - collect work done, compare to plan, write daily notes
---

End-of-day review session. Follow these steps in order:

## 1. Gather Session Data

Collect today's Claude Code session summaries:
```bash
bash scripts/gather-sessions.sh
```

## 2. Gather Git Activity

Collect today's commits across all repos:
```bash
bash scripts/gather-git-activity.sh
```

## 3. Ticket Status Sweep

Extract Jira ticket references from today's work and check their current status:
```bash
bash scripts/ticket-status-sweep.sh
```

If any scripts are not yet available, this step will show an error. Continue regardless.

## 4. Gather RFC Status

Check current RFC status:
```bash
bash scripts/gather-rfcs.sh
```

## 5. Read Today's Plan

Load this morning's plan for comparison:
```bash
bash scripts/read-plan.sh
```

## 6. Read Context

```bash
cat context.md
```

## 7. Review the Day

Using the work-planner agent behavior, present:

1. **Work Summary** - What was actually done today, grouped by project/repo
2. **Plan vs Actual** - Compare morning plan items to what happened. Assign final status to each plan_item: [done], [in-progress], [blocked], [carry-over], [unplanned], [dropped]
3. **Unplanned Work** - Things that came up that weren't in the plan
4. **Ticket Status** - For tickets worked on today that are still Open/To Do in Jira, ask: "Did you update [TICKET]?"
5. **RFC Updates** - Any RFC status changes today (approved, rejected, etc.)
6. **Carry-forward** - Build the carry_forward list for tomorrow:
   - `done` or `dropped`: do NOT add
   - `carry-over`: add with `carried_days` incremented
   - `blocked`: add with `blocked: true` and `blocked_reason` (ask if not known)
   - `unplanned`: ask user if it should carry forward
   - Personal tasks: ask about unfinished ones (drop or carry?)
7. **Key Outcomes** - Brief summary suitable for a status update to manager

Then ask the user:
- Anything to add? Blockers, wins, context?

## 8. Plan Tomorrow

Ask: **"Do you want to plan tomorrow now?"**

If yes:
1. Present the carry-forward list from step 7 as the starting point
2. Ask what else needs to be on tomorrow's agenda (new tickets, meetings, deadlines)
3. Ask which items should have time blocks (HH:MM-HH:MM format) vs float
4. Collect estimated durations for unblocked items
5. Build the tomorrow plan structure (same schema as today's plan)

Get the current time to anchor tomorrow's date:
```bash
date +%Y-%m-%d
```

Save tomorrow's plan to `{output_dir}/YYYY/MM/YYYY-MM-DD-plan.md` with YAML frontmatter — same schema as today's plan but with tomorrow's date. Pre-populate `carry_forward` items as `plan_items` with `status: planned`.

If no: skip this step.

## 9. Save Notes

After the user confirms, save to the daily-notes output directory:

```bash
bash scripts/ensure-daily-dir.sh
```

Write the notes to `{output_dir}/YYYY/MM/YYYY-MM-DD-notes.md` with YAML frontmatter followed by the markdown body. The frontmatter must include:
- `date`: today's date
- `carry_forward`: the list built in step 7 (items for tomorrow)
- `plan_items`: all items from the morning plan with their final status values

See the work-planner agent's "Plan File Frontmatter" and "Day-end Status Rules" sections for the exact schema.

## 10. Commit

Auto-commit the day's files in daily-notes:
```bash
bash scripts/commit-daily-notes.sh
```

## 11. Learn

After saving, write memory entries for non-obvious patterns using the project memory system at `~/.claude/projects/.../memory/`. Only save what would not be obvious from reading the code or git history.

Ask yourself:
- Did a task take 2x+ longer than planned? Save a `project` memory with the reason.
- Was there a recurring unplanned interrupt (same person, same type)? Save a `feedback` or `project` memory.
- Did a time block strategy work unusually well or poorly? Save a `feedback` memory.
- Did you learn something about a tool, script, or integration that will affect future sessions? Save a `reference` or `project` memory.

If nothing non-obvious emerged today, skip this step -- do not write memory entries for things that are self-evident or already in the codebase.
