---
name: day-end
description: End of day - collect work done, compare to plan, write daily notes
---

End-of-day review session. Follow these steps in order.

## 1. Read Today's Plan

Load this morning's plan for comparison:

```bash
daily-driver read plan --frontmatter
```

```bash
daily-driver read plan
```

## 2. Gather Session Activity

Collect today's Claude Code session summaries:

```bash
daily-driver gather sessions
```

## 3. Gather Git Activity

Collect today's commits across all repos:

```bash
daily-driver gather git
```

## 4. Review Application Pipeline

Check for follow-ups due and application status changes:

```bash
daily-driver tracker follow-ups --overdue
```

```bash
daily-driver tracker list
```

## 5. Read Context

```bash
daily-driver read context
```

## 6. Review the Day

Using the work-planner agent behavior, present:

1. **Work Summary** — What was actually done today (applications sent, research, follow-ups, interviews)
2. **Plan vs Actual** — Compare morning plan items to what happened. Assign final status to each plan_item: [done], [in-progress], [blocked], [carry-over], [unplanned], [dropped]
   - Use session data and git activity to infer status for items not covered by the plan
3. **Unplanned Work** — Things that came up that weren't in the plan
4. **Tracker Follow-ups** — For tracker entries with overdue follow-ups from step 4, ask: "Follow up on `<tracker-id> - <label>`?" Present as a compact checklist.
5. **Carry-forward** — Build the carry_forward list for tomorrow:
   - `done` or `dropped`: do NOT add
   - `carry-over`: add with `carried_days` incremented
   - `blocked`: add with `blocked: true` and `blocked_reason` (ask if not known)
   - `unplanned`: ask user if it should carry forward
   - Personal items (`type: personal`): carry unfinished ones forward as `carry_forward` entries with `type: personal`, same as work items. Ask about any outstanding 3+ days (drop, schedule, or keep carrying?).
6. **Key Outcomes** — Applications sent, responses received, interviews scheduled, follow-ups completed

Then ask the user:
- Anything to add? Blockers, wins, context?

## 7. Update Tracker

For any follow-ups completed during today's review, update the tracker entry:

```bash
daily-driver tracker update <ID> --extra follow_up_date=$(date +%Y-%m-%d)
```

For status changes to tracker entries:

```bash
daily-driver tracker update <ID> --status <new-status> --note "<note>"
```

## 8. Plan Tomorrow

Ask: **"Do you want to plan tomorrow now?"**

If yes:
1. Present the carry-forward list from step 6 as the starting point
2. Ask what else needs to be on tomorrow's agenda (new tickets, meetings, deadlines)
3. Ask which items should have time blocks (HH:MM-HH:MM format) vs float
4. Collect estimated durations for unblocked items
5. Build the tomorrow plan structure (same schema as today's plan)

Get tomorrow's date:

```bash
date -v+1d +%Y-%m-%d
```

Ensure tomorrow's daily directory exists:

```bash
daily-driver ensure-daily-dir --date $(date -v+1d +%Y-%m-%d)
```

Save tomorrow's plan to `{output_dir}/YYYY/MM/YYYY-MM-DD-plan.md` with YAML frontmatter — same schema as today's plan but with tomorrow's date. Pre-populate `carry_forward` items as `plan_items` with `status: planned`.

If no: skip this step.

## 9. Save Notes

After the user confirms, ensure today's daily directory exists:

```bash
daily-driver ensure-daily-dir
```

Write the notes to `{output_dir}/YYYY/MM/YYYY-MM-DD-notes.md` with YAML frontmatter followed by the markdown body. The frontmatter must include:

- `date`: today's date
- `carry_forward`: the list built in step 6 (items for tomorrow, both work and personal with `type: personal`)
- `plan_items`: all items from the morning plan (work and personal) with their final status values

See the work-planner agent's "Plan File Frontmatter" and "Day-end Status Rules" sections for the exact schema.

## 10. Learn

After saving, write memory entries for non-obvious patterns using the project memory system at `~/.claude/projects/.../memory/`. Only save what would not be obvious from reading the code or git history.

Ask yourself:
- Did a task take 2x+ longer than planned? Save a `project` memory with the reason.
- Was there a recurring unplanned interrupt (same person, same type)? Save a `feedback` or `project` memory.
- Did a time block strategy work unusually well or poorly? Save a `feedback` memory.
- Did you learn something about a tool or integration that will affect future sessions? Save a `reference` or `project` memory.

If nothing non-obvious emerged today, skip this step — do not write memory entries for things that are self-evident or already in the codebase.
