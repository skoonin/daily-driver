# daily-driver CLI tree

Orientation reference only — use this to find a flag name at a glance.
For non-obvious flag behavior see [commands.md](commands.md); for the
daily flow and worked examples see [usage.md](usage.md); for the full
docs index see [README.md](README.md).

Global flags (registered via `cli/_common.py:add_global_flags(parser)`, called
at the END of the top-level parser and every leaf so they render under a
"global options" group at the BOTTOM of `--help`):

- `-v, --verbose` — Increase logging detail (`-v` = INFO, `-vv` = DEBUG)
- `-q, --quiet` — Errors only (suppresses non-error status/log output; mutually exclusive with `-v`)
- `--no-color` — Disable Rich color/formatting output
- `-w, --workspace PATH` — Path to daily-driver workspace root
- `--version` — Print version and exit (top-level only)

> Both `daily-driver -v tracker list` and `daily-driver tracker list -v` parse correctly. Global args use `default=argparse.SUPPRESS`, so a value set at one level is never silently clobbered by a later level's default — reads must use `getattr(args, "workspace", None)`.
>
> **Date specifiers convention**: `--since SPEC` is forward-looking (matches entries with `date >= SPEC`). `--older-than SPEC` is backward-looking (matches `date < SPEC`). Same grammar via `core.dates.parse_since`: `today | yesterday | tomorrow`, `week | month | quarter | year`, `Nd | Nw | Nm | Ny`, `YYYY-MM-DD`.

```text
daily-driver/
├── (global flags)
│   ├── -v, --verbose (repeat for more detail)
│   ├── -q, --quiet
│   ├── --no-color
│   ├── -w, --workspace PATH
│   └── --version
├── init
│   ├── [path=.]
│   └── -f, --force
├── doctor
│   ├── --fix
│   └── --reset
├── tracker
│   ├── add
│   │   ├── -c, --category CAT (required)
│   │   ├── -T, --title TEXT (required)
│   │   ├── -s, --status STATUS
│   │   ├── -t, --tags a,b
│   │   ├── -l, --link URL
│   │   ├── -N, --note TEXT
│   │   ├── --next-action TEXT
│   │   ├── -d, --due YYYY-MM-DD
│   │   └── --extra KEY=VALUE (repeatable)
│   ├── update
│   │   ├── id (positional)
│   │   ├── -s, --status STATUS
│   │   ├── -N, --note TEXT
│   │   ├── --next-action TEXT
│   │   ├── -t, --tags a,b
│   │   └── --extra KEY=VALUE (repeatable)
│   ├── delete
│   │   └── id (positional)
│   ├── prune
│   │   ├── -c, --category CAT
│   │   ├── -s, --status STATUS
│   │   ├── --older-than SPEC
│   │   └── -n, --dry-run
│   ├── show
│   │   ├── id (positional)
│   │   └── -j, --json
│   ├── list
│   │   ├── -c, --category CAT
│   │   ├── -s, --status FILTER
│   │   ├── -t, --tag TAG
│   │   ├── --since SPEC
│   │   └── -j, --json
│   ├── follow-ups
│   │   ├── --overdue
│   │   └── -j, --json
│   └── stats
│       └── -j, --json
├── status
│   └── -j, --json
├── focus
│   ├── on
│   │   ├── --for DURATION
│   │   └── --reason TEXT
│   ├── off
│   └── status
│       └── -j, --json
├── jobs
│   ├── run
│   │   ├── -n, --dry-run
│   │   ├── --backfill
│   │   ├── -S, --sources LIST
│   │   └── --list-sources
│   ├── status
│   │   └── -j, --json
│   └── prune
│       ├── --older-than SPEC (required)
│       ├── -s, --status STATUS (repeatable; default: dropped, rejected, closed)
│       └── -n, --dry-run
├── paths
│   ├── [kind: root|output|state|ephemeral|daily|daily-plan|daily-notes|daily-state]
│   ├── -d, --date YYYY-MM-DD
│   └── -j, --json
├── gather
│   ├── calendar
│   │   ├── --since SPEC
│   │   ├── --until SPEC
│   │   └── -j, --json
│   └── git
│       ├── --repo PATH
│       ├── --since SPEC
│       ├── --until SPEC
│       └── -j, --json
├── day-start
│   ├── --session-name NAME
│   ├── --agent NAME (default: work-planner)
│   └── --model {sonnet,opus,haiku}
├── day-end
│   ├── --session-name NAME
│   ├── --agent NAME (default: work-planner)
│   └── --model {sonnet,opus,haiku}
├── check-in
│   ├── --session-name NAME
│   ├── --agent NAME (default: work-planner)
│   ├── --model {sonnet,opus,haiku}
│   └── --no-resume
├── summary
│   ├── -r, --range SPEC (required)
│   ├── --detail {low,med,high}
│   ├── --match KW (repeatable)
│   ├── -j, --json
│   ├── --no-clipboard
│   ├── --session-name NAME
│   ├── --agent NAME (default: work-planner)
│   ├── --model {sonnet,opus,haiku}
│   └── --timeout SECONDS
├── scheduler
│   ├── install
│   ├── uninstall
│   └── status [-j, --json]
├── voice-update
│   ├── --from PATH ... (required, repeatable)
│   ├── --append | --replace
│   ├── -n, --dry-run
│   ├── --no-clipboard
│   ├── --session-name NAME
│   ├── --model {sonnet,opus,haiku}
│   └── --timeout SECONDS
└── help
    ├── [topic: commands|statuses|categories|sources|dates|cadences]
    └── -j, --json
```
