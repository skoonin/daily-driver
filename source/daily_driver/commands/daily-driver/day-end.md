---
name: day-end
description: End of day - collect work done, compare to plan, write daily notes
---

End-of-day review session. Follow these steps in order.

## 1. Read Today's Plan

Resolve today's plan path and Read it with the Claude Read tool:

```bash
daily-driver paths daily-plan
```

Read the file at the printed path. Parse the YAML frontmatter for `plan_items` and read the markdown body for any commentary the planner wrote. If the file does not exist, note that no plan baseline exists for today and proceed.

## 2. Gather Session Activity

For Claude Code sessions, list recent JSONL files directly rather than scanning the full project tree:

```bash
ls -t ~/.claude/projects/*/*.jsonl 2>/dev/null | head -20
```

Read the most recent few whose mtimes fall in today's window. Skim for cwd + first user prompt to identify what was worked on.

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

Resolve the workspace output directory and Read `context.md` from it:

```bash
daily-driver paths output
```

Read `<output>/context.md` with the Claude Read tool. If it is missing, note it and proceed.

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

Resolve tomorrow's canonical plan path; the parent directory is created inline:

```bash
TOMORROW=$(date -v+1d +%Y-%m-%d)
TOMORROW_PLAN=$(daily-driver paths daily-plan --date "$TOMORROW")
mkdir -p "$(dirname "$TOMORROW_PLAN")"
echo "$TOMORROW_PLAN"
```

Write tomorrow's plan to `$TOMORROW_PLAN` with YAML frontmatter — same schema as today's plan but with tomorrow's date. Pre-populate `carry_forward` items as `plan_items` with `status: planned`.

If no: skip this step.

## 9. Save Notes

After the user confirms, resolve today's canonical notes path; create the parent directory inline:

```bash
NOTES_PATH=$(daily-driver paths daily-notes)
mkdir -p "$(dirname "$NOTES_PATH")"
echo "$NOTES_PATH"
```

Write the notes to `$NOTES_PATH` with YAML frontmatter followed by the markdown body. The frontmatter must include:

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
