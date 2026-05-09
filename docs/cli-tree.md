# daily-driver CLI tree

Generated 2026-05-04 from `source/daily_driver/cli/`. Snapshot of the v0.1.x command surface for use as a planning reference.

Global flags (registered via `cli/_common.py:add_global_flags(parser)`, called at the END of the top-level parser and every leaf so they render under a "global options" group at the BOTTOM of `--help`):

- `-v, --verbose` — Enable debug-level logging
- `-q, --quiet` — Suppress all output below WARNING (mutually exclusive with `-v`)
- `--no-color` — Disable Rich color/formatting output
- `--workspace PATH` — Path to daily-driver workspace root
- `--version` — Print version and exit (top-level only)

> Both `daily-driver -v tracker list` and `daily-driver tracker list -v` parse correctly. Global args use `default=argparse.SUPPRESS`, so a value set at one level is never silently clobbered by a later level's default — reads must use `getattr(args, "workspace", None)`.
>
> **Date specifiers convention**: `--since SPEC` is forward-looking (matches entries with `date >= SPEC`). `--older-than SPEC` is backward-looking (matches `date < SPEC`). Same grammar via `core.dates.parse_since`: `today | yesterday | tomorrow`, `week | month | quarter | year`, `Nd | Nw | Nm | Ny`, `YYYY-MM-DD`.
>

```text
daily-driver/
├── (global flags)
│   ├── -v, --verbose
│   ├── -q, --quiet
│   ├── --no-color
│   ├── --workspace PATH
│   └── --version
├── init
│   ├── [path=.]
│   └── -f, --force
├── doctor
│   ├── --fix
│   └── --reset
├── tracker
│   ├── add
│   │   ├── --category CAT (required)
│   │   ├── --title TEXT (required)
│   │   ├── --status STATUS
│   │   ├── --tags a,b
│   │   ├── --link URL
│   │   ├── --note TEXT
│   │   ├── --next-action TEXT
│   │   ├── --due YYYY-MM-DD
│   │   └── --extra KEY=VALUE (repeatable)
│   ├── update
│   │   ├── id (positional)
│   │   ├── --status STATUS
│   │   ├── --note TEXT
│   │   ├── --next-action TEXT
│   │   ├── --tags a,b
│   │   └── --extra KEY=VALUE (repeatable)
│   ├── list
│   │   ├── --category CAT
│   │   ├── --status FILTER
│   │   ├── --tag TAG
│   │   ├── --since SPEC
│   │   └── --json
│   ├── follow-ups
│   │   ├── --overdue
│   │   └── --json
│   └── stats
│       └── --json
├── status
│   └── --json
├── focus
│   ├── on
│   │   ├── --for DURATION (required)
│   │   └── --reason TEXT
│   ├── off
│   └── status
│       └── --json
├── jobs
│   ├── run
│   │   ├── -n, --dry-run
│   │   └── --backfill
│   ├── status
│   │   └── --json
│   └── prune
│       ├── --older-than SPEC (required)
│       ├── --status STATUS (repeatable; default: dropped, rejected, closed)
│       └── -n, --dry-run
├── paths
│   ├── [kind: root|output|state|ephemeral|daily|daily-plan|daily-notes]
│   ├── --date YYYY-MM-DD
│   └── --json
├── read
│   ├── context
│   ├── voice-profile
│   └── plan
│       ├── --date YYYY-MM-DD
│       └── --frontmatter
├── ensure-daily-dir
│   └── --date YYYY-MM-DD
├── gather
│   ├── calendar
│   │   ├── --since
│   │   ├── --until
│   │   └── --json
│   ├── git
│   │   ├── --since
│   │   ├── --until
│   │   └── --json
│   ├── sessions
│   │   ├── --since
│   │   ├── --until
│   │   └── --json
│   └── notes
│       ├── --since
│       ├── --until
│       └── --json
├── day-start
│   ├── --session-name
│   ├── --agent (default: work-planner)
│   └── --model
├── day-end
│   ├── --session-name
│   ├── --agent (default: work-planner)
│   └── --model
├── check-in
│   ├── --session-name
│   ├── --agent (default: work-planner)
│   └── --model
├── summary
│   ├── --range SPEC (required)
│   ├── --detail {low,med,high}
│   ├── --match KW (repeatable)
│   ├── --json
│   ├── --no-clipboard
│   ├── --session-name
│   ├── --agent (default: work-planner)
│   ├── --model
│   └── --timeout SECONDS
├── install-scheduler
├── uninstall-scheduler
└── voice-update
    ├── --from PATH ... (required)
    ├── --append | --replace
    ├── -n, --dry-run
    ├── --no-clipboard
    ├── --session-name
    ├── --model
    └── --timeout SECONDS
```
