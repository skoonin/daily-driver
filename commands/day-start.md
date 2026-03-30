---
name: day-start
description: Morning planning - gather context and plan the day
---

Morning planning session. Follow these steps in order:

## 1. Sync Repos

Run the sync script silently:
```bash
bash scripts/sync-repos.sh
```

## 2. Gather Data

Run all three gather scripts and collect their output:
```bash
bash scripts/gather-calendar.sh
```
```bash
bash scripts/gather-jira.sh
```
```bash
bash scripts/gather-prs.sh
```

## 3. Check Yesterday

Look for yesterday's notes file. Calculate yesterday's date (skip weekends):
```bash
YESTERDAY=$(date -v-1d +%Y-%m-%d); DAY=$(date -v-1d +%u); if [ "$DAY" = "1" ]; then YESTERDAY=$(date -v-3d +%Y-%m-%d); fi; YEAR=$(echo $YESTERDAY | cut -d- -f1); MONTH=$(echo $YESTERDAY | cut -d- -f2); cat "$HOME/git/daily-notes/${YEAR}/${MONTH}/${YESTERDAY}-notes.md" 2>/dev/null || echo "(no notes from yesterday)"
```

## 4. Read Context

Read the user's work context:
```bash
cat context.md
```

## 5. Plan the Day

Using the work-planner agent behavior, present:

1. **Today's Schedule** - Meetings and fixed commitments with time blocks
2. **Jira Tickets** - Assigned/in-progress, grouped by instance (IM / SRE), sorted by priority
3. **PR Activity** - Your open PRs and review requests, across both orgs
4. **Carry-over** - Items from yesterday that weren't completed
5. **Proposed Plan** - Time-blocked plan for the day accounting for meetings and buffer

Then ask the user:
- Does this look right?
- Anything to add or reprioritize?
- Any known interrupts or blockers today?

## 6. Save Plan

After the user confirms, save the plan to the daily-notes output directory:

```bash
TODAY=$(date +%Y-%m-%d); YEAR=$(date +%Y); MONTH=$(date +%m); mkdir -p "$HOME/git/daily-notes/${YEAR}/${MONTH}"
```

Write the plan to `~/git/daily-notes/YYYY/MM/YYYY-MM-DD-plan.md` with the agreed content.
