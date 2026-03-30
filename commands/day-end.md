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

## 3. Read Today's Plan

Load this morning's plan for comparison:
```bash
TODAY=$(date +%Y-%m-%d); YEAR=$(date +%Y); MONTH=$(date +%m); cat "$HOME/git/daily-notes/${YEAR}/${MONTH}/${TODAY}-plan.md" 2>/dev/null || echo "(no plan found for today)"
```

## 4. Read Context

```bash
cat context.md
```

## 5. Review the Day

Using the work-planner agent behavior, present:

1. **Work Summary** - What was actually done today, grouped by project/repo, based on session data and git activity
2. **Plan vs Actual** - Compare morning plan to what happened. Mark items: [done], [in-progress], [blocked], [carry-over], [unplanned]
3. **Unplanned Work** - Things that came up that weren't in the plan
4. **Carry-over** - Items to pick up tomorrow
5. **Key Outcomes** - Brief summary suitable for a status update to manager

Then ask the user:
- Anything to add? Blockers, wins, context?
- Any items to flag for tomorrow specifically?

## 6. Save Notes

After the user confirms, save to the daily-notes output directory:

```bash
TODAY=$(date +%Y-%m-%d); YEAR=$(date +%Y); MONTH=$(date +%m); mkdir -p "$HOME/git/daily-notes/${YEAR}/${MONTH}"
```

Write the notes to `~/git/daily-notes/YYYY/MM/YYYY-MM-DD-notes.md` with the reviewed content.

## 7. Commit

Auto-commit the day's files in daily-notes:
```bash
TODAY=$(date +%Y-%m-%d); git -C "$HOME/git/daily-notes" add -A && git -C "$HOME/git/daily-notes" commit -m "daily notes: ${TODAY}" 2>/dev/null || echo "(nothing to commit)"
```

## 8. Learn

After saving, reflect on patterns worth remembering and save to memory:
- Did any tasks take significantly longer/shorter than planned?
- Were there recurring unplanned interrupts?
- What time blocks were most productive?
