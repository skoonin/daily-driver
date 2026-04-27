# Commands

`daily-driver --help` lists every subcommand. `daily-driver <cmd> --help` shows per-command flags. This page covers the non-obvious behavior that isn't in `--help`.

## Global flags

| Flag | Purpose |
|------|---------|
| `-v`, `--verbose` | Debug-level logging |
| `-q`, `--quiet` | Suppress output below WARNING |
| `--no-color` | Disable Rich color |
| `--workspace PATH` | Override CWD-upward workspace discovery |

## Workspace lifecycle

### `init [PATH] [--force]`

Scaffolds a workspace. Static files (`context.md`, `voice-profile.md`) are only written if missing — `--force` does not clobber them. `.dd-config.yaml` is overwritten with a backup. `.claude/commands/daily-driver/` and `.claude/agents/daily-driver/` are always (re-)materialized.

### `doctor [--fix | --reset]`

Runs contract + dependency checks. `--fix` and `--reset` are mutually exclusive. Exit 1 on any ERROR; WARNING exits 0. `--reset` requires a workspace.

### `status [--json]`

Tracker dashboard: totals by category/status, items stalled >14 days (non-terminal), 7-day activity.

## Tracker

YAML-backed store under `<output_dir>/tracker.yaml`. Categories and their `required` field lists are defined in `.dd-config.yaml` under `tracker.categories`. Writes are flock-guarded.

### `tracker add --category CAT --title TEXT [flags]`

| Flag | Description |
|------|-------------|
| `--status STATUS` | Initial status |
| `--tags a,b` | Comma-separated tags |
| `--link URL` | Related URL |
| `--note TEXT` | Free-text note |
| `--next-action TEXT` | Next action |
| `--due YYYY-MM-DD` | Due date |
| `--extra KEY=VALUE` | Repeatable; stored under entry's `extras:` block |

Required fields beyond `title` are validated per-category from config.

### `tracker update ID [flags]`

Same flags as `add`. `--note` **appends** (joined with newlines). `--tags` **replaces** the list. `--extra` **merges** into existing extras.

### `tracker list [--category|--status|--tag|--json]`

Filtered Rich table or JSON.

### `tracker follow-ups [--overdue] [--json]`

Entries with a `next_action` set. `--overdue` filters to those with a past `due` date. Primary view for starting a day.

### `tracker stats [--json]`

Counts grouped by category and status.

## Focus

File-locked toggle that suppresses scheduled check-ins. The lock lives under `.daily-driver/state/`.

```bash
daily-driver focus on --for 90m --reason "deep work"
daily-driver focus status
daily-driver focus off
```

`--for` is required when enabling. Duration: `30m`, `2h`, `1h30m`, or bare minutes. `focus status --json` emits JSON.

## Interactive Claude launchers

Spawn a nested `claude` session with the `work-planner` agent, the workspace as `--add-dir`, and a timestamped session name. Each invokes a slash command:

| Command | Slash command |
|---------|---------------|
| `day-start` | `/day-start` |
| `check-in` | `/check-in` |
| `day-end` | `/day-end` |

Shared flags: `--agent NAME` (default `work-planner`), `--model NAME`, `--session-name NAME`.

## Headless Claude commands

### `summary --range SPEC`

Generates a period summary non-interactively and copies to clipboard.

| Flag | Notes |
|------|-------|
| `--range SPEC` | Required. `today`, `yesterday`, `week`, `month`, `YYYY-MM-DD`, or `YYYY-MM-DD:YYYY-MM-DD` |
| `--detail low\|med\|high` | Verbosity |
| `--match KW` | Repeatable keyword filter |
| `--json` | Emit JSON bundle without invoking claude |
| `--no-clipboard` | Skip `pbcopy` |
| `--timeout SECONDS` | Default 180 |

### `voice-update --from PATH [PATH ...]`

Rewrites `voice-profile.md` from writing samples via headless `claude`.

| Flag | Notes |
|------|-------|
| `--from PATH` | Required; repeatable; files or directories |
| `--append` / `--replace` | Default `--append` |
| `--dry-run` | Print diff only |
| `--no-clipboard`, `--timeout`, `--model`, `--session-name` | As above |

## Job search plugin

Requires `plugins.job_search` in `.dd-config.yaml`. See [configuration.md](configuration.md).

### `scrape-jobs run [--dry-run | --backfill]`

Runs enabled scrapers, appends new rows to `jobs.csv`, enriches via `claude` CLI. `--dry-run` prints matches without writing. `--backfill` re-enriches empty fields on existing rows.

### `scrape-jobs status [--json]`

Reads `jobs-last-run.json` and `jobs.csv` metadata.

## Scheduler (macOS)

### `install-scheduler`

Renders launchd plists into `~/Library/LaunchAgents/` and `launchctl load`s them. Reads `scheduler:` from `.dd-config.yaml` (freeform dict passed to the Jinja template). Defaults: check-in at 11:00 and 15:00, scrape-jobs at 07:00. Idempotent.

### `uninstall-scheduler [--keep-state]`

`launchctl unload` + delete plist. `--keep-state` retains mirrored copies under `.daily-driver/state/launchd/`.

## Scripting helpers

### `paths <kind> [--date YYYY-MM-DD] [--json]`

Prints a resolved workspace path. Kinds: `root`, `output`, `state`, `ephemeral`, `daily`, `daily-plan`, `daily-notes`.

### `read {context,voice-profile,plan} [--date] [--frontmatter]`

Prints workspace text files. `--frontmatter` (on `plan`) emits only the YAML frontmatter.

### `ensure-daily-dir [--date YYYY-MM-DD]`

Creates today's output directory and prints the plan path.

### `gather {calendar,git,sessions,notes} [--since|--until|--json]`

Structured external state readers. `gather git` also accepts `--repo PATH`.
