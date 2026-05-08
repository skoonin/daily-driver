# daily-driver CLI tree

Generated 2026-05-04 from `source/daily_driver/cli/`. Snapshot of the v0.1.x command surface for use as a planning reference.

Global flags (defined on the top-level parser in `cli/cli.py`; **not** propagated via `parents=` to subcommands — they must appear before the subcommand name on the command line):

- `-v, --verbose` — Enable debug-level logging
- `-q, --quiet` — Suppress all output below WARNING (mutually exclusive with `-v`)
- `--no-color` — Disable Rich color/formatting output
- `--workspace PATH` — Path to daily-driver workspace root
- `--version` — Print version and exit

> Note on inheritance: `cli.py` registers each command with `module.add_parser(subparsers, [])` — passing an empty `parents` list. So while the parent parser is built and its globals are parsed at the top level, none of the subcommands declare them. In practice you must write `daily-driver -v tracker list`, not `daily-driver tracker list -v`. Each subcommand's `run()` reads `args.workspace` via `getattr(args, "workspace", None)`, so it does work — but only when supplied before the subcommand.
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
├── scrape-jobs
│   ├── run
│   │   ├── -n, --dry-run
│   │   └── --backfill
│   └── status
│       └── --json
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
│   └── --keep-state
└── voice-update
    ├── --from PATH ... (required)
    ├── --append | --replace
    ├── -n, --dry-run
    ├── --no-clipboard
    ├── --session-name
    ├── --model
    └── --timeout SECONDS
```
