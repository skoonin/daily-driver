---
name: summary
description: Generate a period summary (standup, review, project context) at configurable verbosity
---

Generate a summary of workspace activity over a time range. Use this for
daily standups, weekly reviews, monthly rollups, interview prep, or any
"what have I done lately on X?" question.

## Arguments

- Range (required): `48h`, `7d`, `2w`, `today`, `yesterday`, `this-week`,
  `last-week`, `this-month`, `last-month`, or `YYYY-MM-DD:YYYY-MM-DD`.
- Detail level (optional, default `med`): `low` (terse bullets), `med`
  (organized sections), `high` (narrative with quotes and rationale).
- Keyword filter (optional, repeatable): `--match <keyword-or-tag>` scopes
  gathered rows to ones mentioning the keyword or carrying the `#tag`.

## Workflow

### 1. Confirm the range and mode

Echo the resolved window back to the user before running. Example: "Summarizing
the last 2 weeks (2026-04-08 .. 2026-04-22) at medium detail, no filters."

### 2. Run the summary command

```bash
daily-driver summary --range <spec> --detail <low|med|high> [--match <kw> ...]
```

Pass any `--match` values from the request.

### 3. Present the result

The CLI prints the synthesized summary to stdout and (unless `--no-clipboard`
was set) copies it to the clipboard. Relay the printed output verbatim; do
not paraphrase. If the user asks for refinements, re-run with adjusted flags
rather than editing the output by hand.

### 4. Follow-ups

- If the summary flags a stalled item or an approaching deadline, offer to
  update the tracker via `daily-driver tracker update`.
- If the user wants to save the summary (e.g. a weekly review), suggest a
  filename under the daily directory; they can paste and commit themselves.
