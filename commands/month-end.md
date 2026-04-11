---
name: month-end
description: Monthly rollup - summarize the month's work into a monthly report
---

Generate a monthly summary from this month's weekly summaries and daily notes. Follow these steps in order:

## 1. Determine Month Range

```bash
bash scripts/month-range.sh
```

If today is the 1st and no data exists for the current month, use last month:
```bash
bash scripts/month-range.sh --last
```

## 2. Read Weekly Summaries

Read all weekly summaries that fall within the month:
```bash
bash scripts/list-weekly-summaries.sh
```

Read each weekly summary file found. If no weekly summaries exist, fall back to reading all daily notes for the month.

## 3. Read All Daily Notes

```bash
bash scripts/gather-notes-range.sh "$FIRST_DAY" "$LAST_DAY" all
```

## 4. Read Context

```bash
cat context.md
```

## 5. Generate Monthly Summary

Using the work-planner agent behavior, synthesize weekly summaries and daily notes into a monthly report:

- **Executive Summary**: 1 paragraph describing the month's focus areas and key outcomes
- **Accomplishments**: applications sent, interviews completed, offers/rejections, key milestones
- **Ongoing Work**: active applications, pending follow-ups carrying into next month
- **Key Metrics**:
  - Working days with plans/notes (e.g., 18/22)
  - Total applications sent
  - Response rate (responses / applications)
  - Interviews scheduled / completed
  - Unplanned work percentage
- **Themes and Patterns**: which sources yield responses, company types that ghost, productivity observations
- **Next Month Setup**: follow-ups due, upcoming interviews, carry-over items

This is a higher-level view than the weekly -- aggregate, don't list every application.

## 6. Save Monthly Summary

```bash
bash scripts/monthly-save-path.sh
```

Write the monthly summary to `{output_dir}/{save_dir}/YYYY/YYYY-MM-MonthName-month.md`.

## 7. Commit

```bash
bash scripts/commit-notes.sh monthly
```
