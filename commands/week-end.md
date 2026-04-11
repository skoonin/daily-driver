---
name: week-end
description: Weekly rollup - summarize the week's work into a weekly report
---

Generate a weekly summary from this week's daily notes and plans. Follow these steps in order:

## 1. Determine Week Range

Calculate ISO week Monday-Friday:
```bash
bash scripts/week-range.sh
```

If today is Monday and no notes exist for the current week yet, look back to last week instead:
```bash
bash scripts/week-range.sh --last
```

## 2. Read All Notes and Plans

```bash
bash scripts/gather-notes-range.sh "$WEEK_MON" "$WEEK_FRI" all
```

If Monday with no current week data, use last week's range instead.

## 3. Read Context

```bash
cat context.md
```

## 4. Generate Weekly Summary

Using the work-planner agent behavior, synthesize all daily notes and plans into a weekly report:

- **Summary**: 3-5 sentence paragraph of the week's focus and outcomes
- **Completed This Week**: items marked [done] across all daily notes
- **In Progress / Carries to Next Week**: items marked [in-progress] or [carry-over]
- **Unplanned Work**: items marked [unplanned] that appeared during the week
- **Key Metrics**:
  - Days with notes (e.g., 4/5)
  - Planned vs completed ratio
  - Applications sent this week
  - Responses received / interviews scheduled
  - Unplanned item count
- **Next Week Setup**: carry-over items, known interviews/deadlines, suggested priorities

If today is not Friday, label the report "Week in Progress" and note which days remain.

## 5. Save Weekly Summary

Compute the save path and create the directory:
```bash
bash scripts/weekly-save-path.sh
```

Write the weekly summary to the computed path `{output_dir}/{save_dir}/YYYY/YYYY-WNN-week.md`.

## 6. Commit

Auto-commit the weekly summary in the output directory:
```bash
bash scripts/commit-notes.sh weekly
```
