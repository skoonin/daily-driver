---
name: month-end
description: Monthly rollup - summarize the month's work into a monthly report
---

Generate a monthly summary from this month's weekly summaries and daily notes. Follow these steps in order:

## 1. Determine Month Range

```bash
YEAR=$(date +%Y); MONTH=$(date +%m); MONTH_NAME=$(date +%B); FIRST_DAY="${YEAR}-${MONTH}-01"; LAST_DAY=$(date -j -v1d -v+1m -v-1d +%Y-%m-%d); echo "year=${YEAR} month=${MONTH} name=${MONTH_NAME} first=${FIRST_DAY} last=${LAST_DAY}"
```

If today is the 1st and no data exists for the current month, use last month:
```bash
YEAR=$(date -v-1m +%Y); MONTH=$(date -v-1m +%m); MONTH_NAME=$(date -v-1m +%B); FIRST_DAY="${YEAR}-${MONTH}-01"; LAST_DAY=$(date -v-1d +%Y-%m-%d); echo "last_month: year=${YEAR} month=${MONTH} name=${MONTH_NAME} first=${FIRST_DAY} last=${LAST_DAY}"
```

## 2. Read Weekly Summaries

Read all weekly summaries that fall within the month:
```bash
OUTPUT_DIR=$(yq '.output_dir' config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; SAVE_DIR=$(yq '.reporting.week.save_dir // "weekly"' config.yaml); ls "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}/${YEAR}-W"*"-week.md" 2>/dev/null
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
- **Accomplishments**: major items completed, grouped by theme (infra, monitoring, automation, etc.)
- **Ongoing Work**: items still in progress carrying into next month
- **Key Metrics**:
  - Working days with plans/notes (e.g., 18/22)
  - Tickets completed vs carried forward
  - PRs merged/reviewed
  - Unplanned work percentage
- **Themes and Patterns**: recurring topics, time sinks, productivity observations
- **Next Month Setup**: known upcoming work, carry-over items, deadlines

Keep the tone manager-friendly. This is a higher-level view than the weekly -- aggregate, don't list every ticket.

## 6. Save Monthly Summary

```bash
OUTPUT_DIR=$(yq '.output_dir' config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; SAVE_DIR=$(yq '.reporting.month.save_dir // "monthly"' config.yaml); mkdir -p "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}"; echo "${OUTPUT_DIR}/${SAVE_DIR}/${YEAR}/${YEAR}-${MONTH}-${MONTH_NAME}-month.md"
```

Write the monthly summary to `{output_dir}/{save_dir}/YYYY/YYYY-MM-MonthName-month.md`.

## 7. Commit

```bash
OUTPUT_DIR=$(yq '.output_dir' config.yaml); OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"; YEAR=$(date +%Y); MONTH=$(date +%m); MONTH_NAME=$(date +%B); git -C "$OUTPUT_DIR" add -A && git -C "$OUTPUT_DIR" commit -m "monthly summary: ${YEAR}-${MONTH} ${MONTH_NAME}" 2>/dev/null || echo "(nothing to commit)"
```
