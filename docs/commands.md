# Commands

`daily-driver --help` lists every subcommand. `daily-driver <cmd> --help` shows per-command flags. `daily-driver help [TOPIC]` is the in-CLI reference for discoverable values (statuses, categories, sources, date grammar, cadences). This page covers the non-obvious behavior that isn't in `--help`.

> For an end-user walkthrough of the daily flow, start with [usage.md](usage.md). For configuration, see [configuration.md](configuration.md).

## Global flags

| Flag | Purpose |
|------|---------|
| `-v`, `--verbose` | Increase logging detail (`-v` = INFO, `-vv` = DEBUG). Repeatable, mutually exclusive with `-q` |
| `-q`, `--quiet` | Errors only (suppresses non-error status/log output) |
| `--no-color` | Disable Rich color (markup and highlighting stay on) |
| `-w`, `--workspace PATH` | Override CWD-upward workspace discovery |

### Output streams

Daily Driver splits output across two streams:

- **stdout** — data payloads only (tables, JSON, `paths` output, summary text). Safe to pipe into `jq`, `grep`, `pbcopy`, or a file.
- **stderr** — every status line, warning, and error, plus all log output (INFO/DEBUG when `-v`/`-vv` is set).

This means `daily-driver tracker list -j | jq` and `daily-driver paths daily-plan | xargs $EDITOR` work cleanly while status messages still print to your terminal. Add `2>/dev/null` if you want to silence stderr in scripts, or use `-q` to suppress non-error chatter at the source.

`-q` and `-v` apply to both the program's own status lines and to Python logger output; the two are aligned so a single flag controls verbosity end-to-end.

## Short flags

Short flags follow Unix conventions and are scoped per-subcommand. The mapping below covers every short flag in the CLI; `daily-driver help` is the runtime equivalent.

Reserved (do not redefine): `-h` (argparse help), `-n` (`--dry-run`), `-f` (`--force` on `init`), `-v`/`-q` (verbosity).

| Subcommand | Long flag | Short |
| --- | --- | --- |
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
| `jobs run` | `--json` | `-j` |
| `jobs promote` | `--dry-run` | `-n` |
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

Statuses are free-form, but `tracker add` / `tracker update` print a one-line stderr nudge when the value is outside the recommended set (`open`, `in-progress`, `blocked`, `done`, `ruled-out`) and not already used by another entry in the workspace. Status spelling is normalized (case-folded, underscores and spaces turned into hyphens), so `Ruled_Out` and `ruled-out` are the same status and neither warns. Extend the recommended set with `tracker.extra_statuses` in `.dd-config.yaml`; the `job` category instead uses the job-search lifecycle (`found`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, `closed`). Set `tracker.warn_unknown_status: false` to silence the nudge.

### `tracker add --category CAT --title TEXT [flags]`

| Flag | Description |
| --- | --- |
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
| --- | --- |
| `day-start` | `/day-start` |
| `check-in` | `/check-in` |
| `day-end` | `/day-end` |

Shared flags: `--agent NAME` (default `work-planner`), `--model NAME`, `--session-name NAME`.

`check-in` additionally accepts `--no-resume`, which starts a fresh Claude session instead of resuming the day-start session (controlled by `claude.resume_check_in` in `.dd-config.yaml`).

### In-session slash commands

These ship to the workspace `.claude/commands/daily-driver/` tree but are not exposed as CLI launchers — invoke them inside an existing Claude session.

| Slash command | Purpose |
| --- | --- |
| `/daily-learning` | 15-30 minute learning drill (behavioral STAR, technical fundamentals, system design — and other topics over time). Rotates focus by day of week, avoids repeating recent topics, appends a short practice log to `<output>/interview-practice/<date>.md`. Offered as an optional step inside `/day-start`; can also be run standalone. Renamed from `/interview-prep`. |

## Headless Claude commands

### `summary --range SPEC`

Generates a period summary non-interactively and copies to clipboard.

| Flag | Notes |
| --- | --- |
| `--range SPEC` | Required. `today`, `yesterday`, `week`, `month`, `YYYY-MM-DD`, or `YYYY-MM-DD:YYYY-MM-DD` |
| `--detail low\|med\|high` | Verbosity |
| `--match KW` | Repeatable keyword filter |
| `--json` | Emit JSON bundle without invoking claude |
| `--no-clipboard` | Skip `pbcopy` |
| `--timeout SECONDS` | Default 180 |

### `voice-update --from PATH [PATH ...]`

Rewrites `voice-profile.md` from writing samples via headless `claude`.

| Flag | Notes |
| --- | --- |
| `--from PATH` | Required; repeatable; files or directories |
| `--append` / `--replace` | Default `--append` |
| `-n`, `--dry-run` | Print diff only |
| `--no-clipboard`, `--timeout`, `--model`, `--session-name` | As above |

## Job search plugin

Requires `plugins.job_search` in `.dd-config.yaml`. See [configuration.md](configuration.md).

The `jobs.csv` `Status` column shares the tracker's status machinery. The recommended job-search set is `found`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, `closed`; a freshly scraped row is `found`, and a deliberately blank cell stays blank. Status spelling is normalized to canonical form (case-folded, underscores and spaces turned into hyphens, so `Ruled_Out` becomes `ruled-out`) — spelling only, never meaning. Reading `jobs.csv` never rewrites it: a row's Status is only canonicalized when that row is already being rewritten anyway. That happens in `jobs backfill` and `jobs prune` (which lift stored rows through the model), so those commands print a one-line notice when they fix any spellings (e.g. `Canonicalized 3 status spelling(s) (e.g. Ruled_Out -> ruled-out)`). A normal `jobs run` append carries pre-existing rows through untouched and writes `found` for every new row, so its writes never canonicalize old rows. A status outside the recommended set logs one WARNING naming the offending values — meaningful for hand-edited cells and `backfill`/`prune` over older data, since fresh appends are always `found`.

### `jobs run [-n|--dry-run | -j|--json] [--no-enrich] [--sources LIST | --list-sources]`

Runs enabled scrapers, appends new rows to `jobs.csv`, and enriches missing fields via the provider configured under `plugins.job_search.enrichment.provider` (`claude` by default; `ollama` if set — see [ollama-setup.md](ollama-setup.md)). `--dry-run` prints matches without writing. `--no-enrich` appends the scraped rows but skips all three enrichment phases (detail pages, company products, fit/notes) — no enrichment bars render and no detail-page or LLM calls are made, for a fast/cheap run you can fill later with `jobs backfill`. `-S` / `--sources a,b,c` overrides the enabled set in `.dd-config.yaml` for a single run; `--list-sources` prints the available source names and exits. `-j` / `--json` emits the run manifest (the `jobs-last-run.json` content) to stdout after the run for scripting; it suppresses the live progress block and keeps all diagnostics on stderr, so stdout stays clean JSON for `jq`. `--json` and `--dry-run` are mutually exclusive (the parser rejects the combination with exit 2): both own the stdout channel and a dry-run writes no manifest, so combining them would mix a human table with the JSON contract. On a Ctrl-C / `SIGTERM` under `--json`, the interrupted manifest is still emitted to stdout (exit `130` / `143`).

`jobs run` writes as it works, so an interrupted run keeps what it finished. Each source's new rows are appended to `jobs.csv` as that source completes, and enrichment rewrites the file as it progresses (after each phase and periodically within the long LLM phases). A Ctrl-C, a scheduled-run `SIGTERM`, or a crash drains in-flight work, saves it, and exits — losing at most the one source still scraping or the last enrichment batch. Run `jobs backfill` to finish the empty enrichment fields. Exit codes follow convention: `0` when every source succeeded and saves were clean, `1` when any source failed or persistence degraded during the run (even if the final save recovered the data), `130` for Ctrl-C (`SIGINT`), `143` for `SIGTERM`.

Available sources: `apple`, `greenhouse`, `hn_jobs`, `hn_who_is_hiring`, `indeed`, `linkedin`, `remoteok`, `weworkremotely`. `linkedin` and `indeed` are site-named selectors (e.g. `-S linkedin` or `-S linkedin,indeed`); when both run with equal query knobs they share one merged backend request, otherwise they run separately. Run `daily-driver help sources` for the same list at runtime.

On a TTY, `jobs run` pins a live progress display at the bottom of the terminal: a `Scraping sources` header bar (green ok / red failed segments) plus one progress bar per source, and an `Enriching jobs` group. Every source is its own bar that fills as it works and stays pinned at its result (`linkedin + indeed  61 found` when both run merged, or the single site name, red on failure); each reports against its natural unit — search term × country for LinkedIn/Indeed and Apple, boards for Greenhouse, categories for WeWorkRemotely, a single fetch for the rest. When a source finishes, its bar re-colours into a breakdown of what it found — new (green), already in `jobs.csv` (magenta), skipped by location (yellow), and the duplicate/url-less remainder (grey). Known-slow boards show a one-time expectation note (e.g. `linkedin + indeed  running -- can take several minutes`) until real progress arrives. It ends with a `Completed:` summary line reconciling totals (found, new, matched location, plus `(N skipped by location)` when rows were dropped for location). The bars render on any TTY at normal and `-v`/`-vv` verbosity; verbosity controls only how much scrolls above them — warnings at normal, INFO heartbeats and per-source timings at `-v`, the full stream at `-vv`. Quiet (`-q`) suppresses the live display and every progress line, leaving only errors. Problems surface live above the bars as they happen, with a terse `Warnings: N (shown above)` line at the end. Non-interactive output (cron, launchd, pipes) — or a terminal that doesn't answer the cursor-position query on entry — falls back to plain lines with no live display. During the serial Apple scrape phase the browser runs headless whenever the live display is active.

### `jobs backfill [-n|--dry-run] [--limit N]`

Re-enriches empty enrichment fields (Product/Purpose, GD Rating, Fit, Notes) on existing `jobs.csv` rows without scraping. It shares the same enrichment driver as `jobs run`: a `Job backfill` live progress block with the same phase rows (Detail pages / Company products / Fit and notes), the overlapped product + fit/notes coordinator under one shared concurrency cap, periodic saves every ~25 results, and the ollama reachability preflight before any LLM call. Rows already filled — and rows in a skip status — are left untouched, and the counts respect the `enrich_product` / `enrich_gd_rating` toggles (a disabled axis reports zero need; Fit and Notes are one combined need, since a single call fills both). `-n` / `--dry-run` reports the per-phase would-enrich counts and makes no LLM calls and no writes. `--limit N` caps LLM spend for this invocation by bounding both the product and fit/notes budgets at `N` (minimum 1; default: the configured per-phase caps). A pre-mutation backup is written under `backups/` lazily — only when a write actually happens — so a no-op backfill (e.g. the ollama server is down and nothing changes) leaves `jobs.csv` untouched (bytes and mtime) and writes no backup. A Ctrl-C or `SIGTERM` saves partial progress and names the backup. Exit codes: `130` for `SIGINT`, `143` for `SIGTERM`.

backfill holds the jobs lock across its whole read → enrich → rewrite window. Because it rewrites the file from an in-memory snapshot (rather than appending), it cannot drop the lock mid-enrichment the way `jobs run` does, or a concurrent run's appended rows would be clobbered by the rewrite.

### `jobs promote URL-OR-COMPANY [-n|--dry-run]`

Promotes a `jobs.csv` row into a tracker `job` entry once it needs active driving (interviews, follow-ups). The tracker is the system that drives work (follow-ups, day-start planning); `jobs.csv` is the discovery / triage record. Promotion is the explicit bridge between them — never automatic.

The selector resolves a single row, in order: an exact match on the row's Link URL (the primary path), then an unambiguous case-insensitive substring of Company. If the substring matches no rows or more than one, the command lists what it found (or says nothing matched) and exits `1` — pass the full Link URL to disambiguate.

The new entry is category `job`, titled `<Company> -- <Role>`, with the job URL stored both as the entry's `link` and in `extras` (alongside `company`, `role`, and `source`). The status carries through from the row's Status unchanged when it is a recognized job status (`found`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, `closed`); a blank Status (or one outside that set) falls back to `applied`, since promotion implies you are now driving the row.

The URL in `extras` is the durable key, so promotion is idempotent: promoting the same URL twice does not create a duplicate — it reports `already promoted as <id>` and exits `0`.

Promotion does NOT mutate `jobs.csv`. The row's Status is yours to drive by hand; `promote` is single-purpose and only ever writes the tracker. `-n` / `--dry-run` prints the entry that would be created and writes nothing.

### `jobs status [--json]`

Reads `jobs-last-run.json` and `jobs.csv` metadata. When the last run was cut short, it prints a recovery line naming the phase it reached and pointing at `jobs backfill` to finish enrichment.

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
