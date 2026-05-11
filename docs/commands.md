# Commands

`daily-driver --help` lists every subcommand. `daily-driver <cmd> --help` shows per-command flags. `daily-driver help [TOPIC]` is the in-CLI reference for discoverable values (statuses, categories, sources, date grammar, cadences). This page covers the non-obvious behavior that isn't in `--help`.

> For an end-user walkthrough of the daily flow, start with [usage.md](usage.md). For configuration, see [configuration.md](configuration.md).

## Global flags

| Flag | Purpose |
|------|---------|
| `-v`, `--verbose` | Debug-level logging |
| `-q`, `--quiet` | Suppress output below WARNING |
| `--no-color` | Disable Rich color |
| `-w`, `--workspace PATH` | Override CWD-upward workspace discovery |

## Short flags

Short flags follow Unix conventions and are scoped per-subcommand. The mapping below covers every short flag in the CLI; `daily-driver help` is the runtime equivalent.

Reserved (do not redefine): `-h` (argparse help), `-n` (`--dry-run`), `-f` (`--force` on `init`), `-v`/`-q` (verbosity).

| Subcommand | Long flag | Short |
|---|---|---|
| (global) | `--workspace` | `-w` |
| `init` | `--force` | `-f` |
| `tracker add` | `--category` | `-c` |
| `tracker add` | `--title` | `-T` |
| `tracker add` | `--status` | `-s` |
| `tracker add` | `--tags` | `-t` |
| `tracker add` | `--link` | `-l` |
| `tracker add` | `--note` | `-N` |
| `tracker add` | `--due` | `-d` |
| `tracker update` | `--status` | `-s` |
| `tracker update` | `--note` | `-N` |
| `tracker update` | `--tags` | `-t` |
| `tracker prune` | `--category` | `-c` |
| `tracker prune` | `--status` | `-s` |
| `tracker prune` | `--dry-run` | `-n` |
| `tracker show` | `--json` | `-j` |
| `tracker list` | `--category` | `-c` |
| `tracker list` | `--status` | `-s` |
| `tracker list` | `--tag` | `-t` |
| `tracker list` | `--json` | `-j` |
| `tracker follow-ups` | `--json` | `-j` |
| `tracker stats` | `--json` | `-j` |
| `status` | `--json` | `-j` |
| `focus status` | `--json` | `-j` |
| `jobs run` | `--sources` | `-S` |
| `jobs run` | `--dry-run` | `-n` |
| `jobs status` | `--json` | `-j` |
| `jobs prune` | `--status` | `-s` |
| `jobs prune` | `--dry-run` | `-n` |
| `paths` | `--date` | `-d` |
| `paths` | `--json` | `-j` |
| `gather calendar` | `--json` | `-j` |
| `gather git` | `--json` | `-j` |
| `scheduler status` | `--json` | `-j` |
| `summary` | `--range` | `-r` |
| `summary` | `--json` | `-j` |
| `voice-update` | `--dry-run` | `-n` |
| `help` | `--json` | `-j` |

Capitals are used where a lowercase letter is already taken on the same subparser (`-T` title vs `-t` tag, `-N` note vs `-n` dry-run, `-S` sources vs `-s` status). Some long flags intentionally have no short — `--repo`, `--reason`, `--for`, `--since`, `--until`, `--older-than`, `--match`, `--detail`, `--model` — to keep the alphabet free for higher-traffic flags.

## Workspace lifecycle

### `init [PATH] [-f | --force]`

Scaffolds a workspace. Idempotent: re-running on the same path fills any missing artifacts and exits 0 with a `Created: ... ; Skipped: ...` summary. Static files (`context.md`, `voice-profile.md`, `.gitignore`) are only written if missing — `--force` does not clobber them. With `--force`, `.dd-config.yaml` is overwritten (the previous contents are preserved as `.dd-config.yaml.bak`). `.claude/commands/daily-driver/` and `.claude/agents/daily-driver/` are always (re-)generated.

### `doctor [--fix | --reset]`

Runs contract + dependency checks. `--fix` and `--reset` are mutually exclusive. Exit 1 on any ERROR; WARNING exits 0. `--reset` requires a workspace. `--fix` prints an action log of every repair it performs (created file, restored permission, regenerated managed file) so the change set is auditable.

### `status [-j | --json]`

Tracker dashboard: setup gaps (printed first when present), totals by category/status, items stalled >14 days (non-terminal), 7-day activity. `--json` emits the full payload including a `setup_gaps` array of `{kind, message}` entries detected from workspace state.

## Discovery

### `help [TOPIC] [-j | --json]`

Reference command — distinct from argparse `--help` (usage). Prints the subcommand list plus discoverable value groups in one place: tracker statuses (recommended + in-use), tracker categories (from `.dd-config.yaml`), scraper sources, date-spec grammar, recurring-task cadences. `TOPIC` filters to a single section: `commands`, `statuses`, `categories`, `sources`, `dates`, `cadences`. `--json` produces a structured payload for tab-completion or scripting.

`help` is workspace-aware but does not require one; without a workspace, `categories` reports a hint and `statuses.in_use` is empty.

## Tracker

YAML-backed store under `<output_dir>/tracker.yaml`. Categories and their `required` field lists are defined in `.dd-config.yaml` under `tracker.categories`. Writes are flock-guarded.

Statuses are free-form, but `tracker add` / `tracker update` print a one-line stderr nudge when the value is outside the recommended set (`open`, `in-progress`, `blocked`, `done`, `ruled-out`) and not already used by another entry in the workspace. Set `tracker.warn_unknown_status: false` in `.dd-config.yaml` to silence.

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

### In-session slash commands

These ship to the workspace `.claude/commands/daily-driver/` tree but are not exposed as CLI launchers — invoke them inside an existing Claude session.

| Slash command | Purpose |
|---------------|---------|
| `/daily-learning` | 5-10 minute learning drill (behavioral STAR, technical fundamentals, system design — and other topics over time). Rotates focus by day of week, avoids repeating recent topics, appends a short practice log to `<output>/interview-practice/<date>.md`. Offered as an optional step inside `/day-start`; can also be run standalone. Renamed from `/interview-prep`. |

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

Runs enabled scrapers, appends new rows to `jobs.csv`, and enriches missing fields via the provider configured under `ai.enrichment.provider` (`claude` by default; `ollama` if set — see [ollama-setup.md](ollama-setup.md)). `--dry-run` prints matches without writing. `--backfill` re-enriches empty fields on existing rows. `-S` / `--sources a,b,c` overrides the enabled set in `.dd-config.yaml` for a single run; `--list-sources` prints the available source names and exits.

Available sources: `apple`, `greenhouse`, `hn_jobs`, `hn_who_is_hiring`, `jobspy`, `remoteok`, `weworkremotely`. Run `daily-driver help sources` for the same list at runtime.

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

## See also

- [usage.md](usage.md) — end-user walkthrough.
- [configuration.md](configuration.md) — `.dd-config.yaml` reference.
- [cli-tree.md](cli-tree.md) — at-a-glance command tree.
