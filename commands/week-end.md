---
name: week-end
description: Weekly rollup - summarize the week's work into a weekly report
---

Generate a weekly summary from this week's daily notes and plans. Follow these steps in order:

## 1. Determine Week Range

Calculate ISO week Monday-Friday:
```bash
DAYS_SINCE_MON=$(($(date +%u) - 1)); WEEK_MON=$(date -j -v-${DAYS_SINCE_MON}d +%Y-%m-%d); WEEK_FRI=$(date -j -v-${DAYS_SINCE_MON}d -v+4d +%Y-%m-%d); DOW=$(date +%u); WEEK_NUM=$(date +%V); YEAR=$(date +%Y); echo "week=W${WEEK_NUM} monday=${WEEK_MON} friday=${WEEK_FRI} dow=${DOW}"
```

If today is Monday and no notes exist for the current week yet, look back to last week instead:
```bash
DAYS_SINCE_MON=$(($(date +%u) - 1)); LAST_MON=$(date -j -v-${DAYS_SINCE_MON}d -v-7d +%Y-%m-%d); LAST_FRI=$(date -j -v-${DAYS_SINCE_MON}d -v-3d +%Y-%m-%d); echo "last_week_monday=${LAST_MON} last_week_friday=${LAST_FRI}"
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
  - Unplanned item count
  - PRs merged/reviewed (from notes)
- **Next Week Setup**: carry-over items, known meetings/deadlines, suggested priorities

If today is not Friday, label the report "Week in Progress" and note which days remain.

## 5. Save Weekly Summary

Compute the save path and create the directory:
```bash
OUTPUT_DIR=$(yq '.output_dir' config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; SAVE_DIR=$(yq '.reporting.week.save_dir // "weekly"' config.yaml); YEAR=$(date +%Y); WEEK_NUM=$(date +%V); mkdir -p "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}"; echo "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}/${YEAR}-W${WEEK_NUM}-week.md"
```

Write the weekly summary to the computed path `{output_dir}/{save_dir}/YYYY/YYYY-WNN-week.md`.

## 6. Commit

Auto-commit the weekly summary in the output directory:
```bash
OUTPUT_DIR=$(yq '.output_dir' config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; YEAR=$(date +%Y); WEEK_NUM=$(date +%V); git -C "$OUTPUT_DIR" add -A && git -C "$OUTPUT_DIR" commit -m "weekly summary: ${YEAR}-W${WEEK_NUM}" 2>/dev/null || echo "(nothing to commit)"
```
