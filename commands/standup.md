---
name: standup
description: Generate async standup summary from yesterday's notes and today's plan
---

Generate an async standup update. Follow these steps in order:

## 1. Resolve Dates

Calculate yesterday (skipping weekends) and determine lookback range based on day of week:
```bash
bash scripts/standup-dates.sh
```

Lookback logic:
- **Monday**: yesterday=Friday, lookback covers Friday only (weekend skipped)
- **Tuesday**: yesterday=Monday, lookback covers Monday only
- **Wednesday**: yesterday=Tuesday, lookback covers Mon-Tue (covers since last standup on Tue)
- **Thursday**: yesterday=Wednesday, lookback covers Wednesday only
- **Friday**: yesterday=Thursday, lookback covers Wed-Thu (covers since last standup on Thu)

## 2. Read Recent Notes

Read all notes files in the lookback range:
```bash
bash scripts/gather-notes-range.sh "$LOOKBACK_START" "$YESTERDAY" notes
```

If no notes found, search back further (up to 5 business days) for the most recent:
```bash
SEARCH_START=$(date -j -v-7d +%Y-%m-%d); bash scripts/gather-notes-range.sh "$SEARCH_START" "$YESTERDAY" notes
```

## 3. Read Today's Plan

```bash
bash scripts/gather-notes-range.sh "$TODAY" "$TODAY" plan
```

## 4. Read Standup Format

```bash
yq '.reporting.standup.format // "slack"' config.yaml
```

## 5. Generate Standup

Using the work-planner agent behavior, generate a standup update:

- For `slack` format: use `*Yesterday*`, `*Today*`, `*Blockers*` as section headers with bullet lists
- For `plain` format: use `## Yesterday`, `## Today`, `## Blockers` with bullet lists

Content guidelines:
- **Yesterday**: completed items from notes across the lookback range, merged PRs, key outcomes
- **Today**: top 3-5 items from plan, important meetings. If no plan exists, note "plan pending".
- **Blockers**: any blocked items from notes carry-forward sections. If none, omit the section entirely.
- Keep to 10-15 lines total. Scannable in 30 seconds. No filler.

## 6. Copy to Clipboard and Save

Copy the standup text to the clipboard:
```bash
pbcopy
```

Pipe the generated standup text into `pbcopy`, then print the full standup text and confirm it has been copied.

If save is enabled, also write to the output directory:
```bash
bash scripts/standup-save-path.sh
```

If `save` is `true`, write the standup to `{output_dir}/YYYY/MM/YYYY-MM-DD-standup.md`.
