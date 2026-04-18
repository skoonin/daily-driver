---
name: standup
description: Generate async standup summary (Yesterday/Today/Blockers) from structured frontmatter
---

Run the standup builder script:

```bash
bash scripts/build-standup.sh
```

The script reads YAML frontmatter from yesterday's notes file and today's plan file,
assembles Y/T/B output, copies it to the clipboard, and exits 0 on success.

If the script exits with code 2, some items have unrecognised status values.
Re-run with the ambiguous flag to isolate them:

```bash
bash scripts/build-standup.sh --ambiguous
```

Take only the lines printed under `AMBIGUOUS_ITEMS:` and resolve them manually,
or ask Claude: "Normalise each of these to a one-line completed action: <paste items>".
Do not send the full notes/plan content to Claude — only the flagged subset.

The assembled standup is already on the clipboard. If the output looks wrong, check
that yesterday's notes file and today's plan file have valid YAML frontmatter with
`plan_items[].status` and `carry_forward[].status` fields.
