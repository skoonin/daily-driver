# Commands

`daily-driver --help` lists every subcommand. `daily-driver <cmd> --help` shows per-command flags. This page covers the non-obvious behavior that isn't in `--help`.

## Global flags

| Flag | Purpose |
|------|---------|
| `-v`, `--verbose` | Debug-level logging |
| `-q`, `--quiet` | Suppress output below WARNING |
| `--no-color` | Disable Rich color |
| `--workspace PATH` | Override CWD-upward workspace discovery |

Several commands also accept short forms of common flags: `-f` aliases `--force` (on `init`), and `-n` aliases `--dry-run` (on `tracker prune`, `jobs run`, `jobs prune`, `voice-update`).

## Workspace lifecycle

### `init [PATH] [-f | --force]`

Scaffolds a workspace. Static files (`context.md`, `voice-profile.md`) are only written if missing — `--force` does not clobber them. `.dd-config.yaml` is overwritten with a backup. `.claude/commands/daily-driver/` and `.claude/agents/daily-driver/` are always (re-)generated.

### `doctor [--fix | --reset]`

Runs contract + dependency checks. `--fix` and `--reset` are mutually exclusive. Exit 1 on any ERROR; WARNING exits 0. `--reset` requires a workspace. `--fix` prints an action log of every repair it performs (created file, restored permission, regenerated managed file) so the change set is auditable.

### `status [--json]`

Tracker dashboard: setup gaps (printed first when present), totals by category/status, items stalled >14 days (non-terminal), 7-day activity. `--json` emits the full payload including a `setup_gaps` array of `{kind, message}` entries detected from workspace state.

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

### `tracker delete ID`

Removes a single entry by ID. Exit 1 if the ID is unknown.

### `tracker prune [--category|--status|--older-than SPEC] [-n|--dry-run]`

Bulk-delete entries matching all provided filters. At least one filter is required (no-filter prune is refused with exit 2). `--older-than` accepts `today`, `yesterday`, `week`, `month`, `quarter`, `year`, `Nd`/`Nw`/`Nm`/`Ny`, or `YYYY-MM-DD`. `--dry-run` lists candidates without deleting.

### `tracker show ID [--json]`

Single-entry read. Rich vertical key/value layout by default, structured JSON with `--json`. Exit 1 with a clear error when the ID is unknown.

### `tracker list [--category|--status|--tag|--since SPEC|--json]`

Filtered Rich table or JSON. `--since` accepts the same grammar as `tracker prune --older-than` and shows entries updated on or after the resolved date.

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

`--for` is optional; when omitted, `focus.default_duration` from `.dd-config.yaml` is used (fallback `25m`). Duration grammar: `30m`, `2h`, `1h30m`, or bare minutes. `focus status --json` emits JSON.

## Interactive Claude launchers

Spawn a nested `claude` session with the `work-planner` agent, the workspace as `--add-dir`, and a timestamped session name. Each invokes a slash command:

| Command | Slash command |
|---------|---------------|
| `day-start` | `/day-start` |
| `check-in` | `/check-in` |
| `day-end` | `/day-end` |

Shared flags: `--agent NAME` (default `work-planner`), `--model NAME`, `--session-name NAME`.

`check-in` additionally accepts `--no-resume`, which starts a fresh Claude session instead of resuming the day-start session (controlled by `claude.resume_check_in` in `.dd-config.yaml`).

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
| `-n`, `--dry-run` | Print diff only |
| `--no-clipboard`, `--timeout`, `--model`, `--session-name` | As above |

## Job search plugin

Requires `plugins.job_search` in `.dd-config.yaml`. See [configuration.md](configuration.md).

### `jobs run [-n|--dry-run] [--backfill] [--sources LIST | --list-sources]`

Runs enabled scrapers, appends new rows to `jobs.csv`, enriches via `claude` CLI. `--dry-run` prints matches without writing. `--backfill` re-enriches empty fields on existing rows. `--sources a,b,c` overrides the enabled set in `.dd-config.yaml` for a single run; `--list-sources` prints the available source names and exits.

### `jobs status [--json]`

Reads `jobs-last-run.json` and `jobs.csv` metadata.

### `jobs prune --older-than SPEC [--status STATUS]... [-n|--dry-run]`

Moves stale rows from `jobs.csv` to `jobs.archive.csv`. `--older-than` is required and accepts the same grammar as `tracker prune --older-than`. `--status` is repeatable; default targets are `dropped`, `rejected`, `closed`. Pass `--status active` to nuke stale active rows.

## Scheduler (macOS)

W8 consolidated the legacy `install-scheduler` / `uninstall-scheduler` top-level commands into a single `scheduler {install,uninstall,status}` group.

### `scheduler install`

Renders launchd plists into `~/Library/LaunchAgents/` and `launchctl load`s them. Reads `scheduler:` from `.dd-config.yaml` (freeform dict passed to the Jinja template). Defaults: check-in at 11:00 and 15:00, jobs at 07:00, day-cycle at `schedule.day_start` / `schedule.day_end` (configurable in `.dd-config.yaml`). Idempotent.

### `scheduler uninstall`

`launchctl unload` + delete plist. State mirror under `.daily-driver/state/launchd/` is always removed.

### `scheduler status [--json]`

Lists configured jobs and whether each plist is currently installed in `~/Library/LaunchAgents/`.

## Scripting helpers

### `paths <kind> [--date YYYY-MM-DD] [--json]`

Prints a resolved workspace path. Kinds: `root`, `output`, `state`, `ephemeral`, `daily`, `daily-plan`, `daily-notes`, `daily-state`.

### `gather {calendar,git} [--since|--until|--json]`

Structured external state readers. `gather git` also accepts `--repo PATH`; without it, repos are discovered under `gather.git.search_paths` from `.dd-config.yaml` (falling back to the current working directory).
