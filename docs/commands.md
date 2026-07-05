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
| `tracker update` | `--title` | `-T` |
| `tracker update` | `--status` | `-s` |
| `tracker update` | `--tags` | `-t` |
| `tracker update` | `--link` | `-l` |
| `tracker update` | `--note` | `-N` |
| `tracker update` | `--due` | `-d` |
| `tracker prune` | `--category` | `-c` |
| `tracker prune` | `--status` | `-s` |
| `tracker prune` | `--dry-run` | `-n` |
| `tracker prune` | `--json` | `-j` |
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
| `jobs backfill` | `--json` | `-j` |
| `jobs discover-boards` | `--json` | `-j` |
| `jobs promote` | `--dry-run` | `-n` |
| `jobs status` | `--json` | `-j` |
| `jobs verify` | `--sources` | `-S` |
| `jobs verify` | `--dry-run` | `-n` |
| `jobs verify` | `--json` | `-j` |
| `jobs prune` | `--status` | `-s` |
| `jobs prune` | `--dry-run` | `-n` |
| `jobs prune` | `--json` | `-j` |
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

### `doctor [--fix | --reset] [-j | --json]`

Runs contract + dependency checks. `--fix` and `--reset` are mutually exclusive. Exit 1 on any ERROR; WARNING exits 0. `--reset` requires a workspace. `--fix` prints an action log of every repair it performs (created file, restored permission, regenerated managed file) so the change set is auditable. `--json` emits a `{schema, data}` envelope whose `data` carries the run `mode` (`check`/`fix`/`reset`), the per-check results (`name`, `status`, `detail`, `fix_hint`), and the overall `exit_code` (the results table is suppressed).

### `status [-j | --json]`

Tracker dashboard: setup gaps (printed first when present), totals by category/status, items stalled >14 days (non-terminal), 7-day activity. `--json` emits the full payload including a `setup_gaps` array of `{kind, message}` entries detected from workspace state.

## Discovery

### `help [TOPIC] [-j | --json]`

Reference command — distinct from argparse `--help` (usage). Prints the subcommand list plus discoverable value groups in one place: tracker statuses (recommended + in-use), tracker categories (from `.dd-config.yaml`), scraper sources, date-spec grammar, recurring-task cadences. `TOPIC` filters to a single section: `commands`, `statuses`, `categories`, `sources`, `dates`, `cadences`. `--json` produces a structured payload for tab-completion or scripting.

`help` is workspace-aware but does not require one; without a workspace, `categories` reports a hint and `statuses.in_use` is empty.

## Tracker

YAML-backed store under `<output_dir>/tracker.yaml`. Categories and their `required` field lists are defined in `.dd-config.yaml` under `tracker.categories`. Writes are flock-guarded.

Statuses are free-form, but `tracker add` / `tracker update` print a one-line stderr nudge for a value outside the recommended set that no other entry uses. Spelling is normalized (case-folded, underscores/spaces to hyphens), so `Ruled_Out` and `ruled-out` are one status and neither warns.

- Default recommended set: `open`, `in-progress`, `blocked`, `done`, `ruled-out`.
- The `job` category uses the job-search lifecycle instead: `found`, `pending`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, `closed`.
- Extend the set with `tracker.extra_statuses`, or silence the nudge with `tracker.warn_unknown_status: false`.

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

### `tracker prune [--category|--status|--older-than SPEC] [-n|--dry-run] [-j|--json]`

Bulk-delete entries matching all provided filters. At least one filter is required (no-filter prune is refused with exit 2). `--older-than` accepts `today`, `yesterday`, `week`, `month`, `quarter`, `year`, `Nd`/`Nw`/`Nm`/`Ny`, or `YYYY-MM-DD`. `--dry-run` lists candidates without deleting. `--json` emits the matched/removed entry set (`dry_run`, `removed`, `count`) in the `{schema, data}` envelope.

### `tracker show ID [--json]`

Single-entry read. Rich vertical key/value layout by default, structured JSON with `--json`. Exit 1 with a clear error when the ID is unknown.

### `tracker list [--category|--status|--tag|--since SPEC|--json]`

Filtered Rich table or JSON. `--since` accepts the same grammar as `tracker prune --older-than` and shows entries updated on or after the resolved date.

### `tracker follow-ups [--overdue] [--json]`

Entries with a `next_action` set. Entries in a terminal status (`done`, `ruled-out`, and the job-terminal set, plus any `tracker.terminal_statuses`) are excluded. `--overdue` filters to those with a past `due` date. Primary view for starting a day.

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
| `day-start` | `/daily-driver:day-start` |
| `check-in` | `/daily-driver:check-in` |
| `day-end` | `/daily-driver:day-end` |

Shared flags: `--agent NAME` (default `work-planner`), `--model NAME`, `--session-name NAME`.

`check-in` additionally accepts `--no-resume`, which starts a fresh Claude session instead of resuming the day-start session (controlled by `claude.resume_check_in` in `.dd-config.yaml`).

### In-session slash commands

These ship to the workspace `.claude/commands/daily-driver/` tree but are not exposed as CLI launchers — invoke them inside an existing Claude session.

| Slash command | Purpose |
| --- | --- |
| `/daily-driver:daily-learning` | 15-30 minute learning drill (behavioral STAR, technical fundamentals, system design — and other topics over time). Rotates focus by day of week, avoids repeating recent topics, appends a short practice log to `<output>/interview-practice/<date>.md`. Offered as an optional step inside `/daily-driver:day-start`; can also be run standalone. |

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

Updates `voice-profile.md` from writing samples via headless `claude`. Default `--append` merges new observations into the profile by section, preserving existing text; `--replace` rewrites the whole profile from scratch.

| Flag | Notes |
| --- | --- |
| `--from PATH` | Required; repeatable; files or directories |
| `--append` / `--replace` | Default `--append` merges new observations by section; `--replace` rewrites the whole profile |
| `-n`, `--dry-run` | Validate sources + print target path; no model call or write |
| `--no-clipboard`, `--timeout`, `--model`, `--session-name` | As above |

## Job search plugin

Requires `plugins.job_search` in `.dd-config.yaml`. See [configuration.md](configuration.md).

The `jobs.csv` `Status` column shares the tracker's status machinery. The recommended job set is `found`, `pending`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, `closed`. A freshly scraped row is `found`; a deliberately blank cell stays blank. Only `found`/`pending` rows are eligible for (re-)enrichment (`jobs run`, `jobs backfill`) — once a row moves to any other status its Fit/Notes/Remote are left alone.

- **Spelling is normalized** (case-folded, underscores/spaces to hyphens, so `Ruled_Out` becomes `ruled-out`) — spelling only, never meaning.
- **Reading never rewrites.** A row's Status is canonicalized only when that row is already being rewritten — i.e. in `jobs backfill` and `jobs prune`, which print a one-line notice when they fix spellings (`Canonicalized 3 status spelling(s) ...`). A normal `jobs run` append leaves pre-existing rows untouched.
- **Out-of-set statuses log one WARNING** naming the offending values — relevant for hand-edited cells and `backfill`/`prune` over older data, since fresh appends are always `found`.

### `jobs run [-n|--dry-run | -j|--json] [--no-enrich] [--sources LIST | --list-sources]`

Runs enabled scrapers, appends new rows to `jobs.csv`, and enriches missing fields via `plugins.job_search.enrichment.provider` (`claude` by default; `ollama` if set — see [ollama-setup.md](ollama-setup.md)). Comp is filled without network cost where possible: pay-transparency text in the scraped description is parsed first (conservative — a salary-anchored, currency-marked, annual-sized figure, else the cell stays blank), and only rows still missing comp fetch their detail page. Greenhouse hosted pages are never fetched (one shared host bot-walls at run volume; the description already carries the pay text).

The run also **verifies closure** for board-backed sources (Greenhouse, Ashby, Lever, Workable, Workday): their scrapes return each company's complete listing, so a stored job absent from a successfully-fetched listing on two consecutive runs is marked `Status: closed` + `Date Closed` (the diff uses the raw pre-role-filter listing; failed, partial, or config-removed boards close nothing; only untriaged `found`/`pending` rows are touched). Closed rows are archived by `jobs prune` and re-surface loudly as `Reopened` if they ever re-appear. Miss counts persist in `board-diff-misses.json`.

After the new rows are enriched, the run also picks up the **backlog**: pre-existing rows that have never been enriched (empty `Date Enriched`, status `found`/`pending`) are scored in place using their cached description from `descriptions.jsonl` — descriptions are never fetched here. New rows get fit-budget priority; the backlog consumes whatever `max_enrich_fit` budget remains, and the summary reports `Backlog: N scored, M remaining` (remaining rows are picked up by the next run or `jobs backfill`). A backlog row with no cached description is reported as `awaiting description` and left alone — a later scrape that re-sights the job heals its description, after which it scores normally. Backlog rows keep their position in `jobs.csv` and are not counted in `new_jobs`; the manifest reports them under `backlog_enriched` / `backlog_remaining`.

- `--dry-run` — print matches without writing.
- `--no-enrich` — append scraped rows but skip enrichment (detail pages and fit/notes), including the backlog pass. Fast and cheap; descriptions captured at scrape are still saved, so a later plain `jobs run` (or `jobs backfill`) finishes those rows.
- `-S` / `--sources a,b,c` — override the enabled set for one run. `--list-sources` prints the names and exits.
- `-j` / `--json` — emit the run manifest (`jobs-last-run.json`) to stdout after the run, wrapped in the standard `{"schema": 1, "data": <manifest>}` envelope (read e.g. `.data.new_jobs`), with the live progress block suppressed and diagnostics on stderr so stdout stays clean for `jq`. Mutually exclusive with `--dry-run` (rejected with exit 2). An interrupt still emits the manifest (exit `130` / `143`); an unreadable manifest emits `{"schema": 1, "data": null}`.
- `.data.sources_degraded` — distinct from `.data.sources_failed`: a source that returned partial or empty-after-failures results is tracked as degraded rather than failed. Its rows are kept and the exit code is unchanged, but it is listed under `sources_degraded` in the manifest and called out in the run summary, so a quietly thin scrape is visible without being treated as an outright failure.
- `.data.saturated_queries` — queries that returned their full result cap, meaning the window held more matches than the run saw. Each entry carries `source`, `query`, `returned`, `requested`, and `kind` (`cap` = the configured per-query cap truncated the results — raise `results_wanted_per_query` or split the term; `plateau` = LinkedIn's ~100-per-IP rate-limit wall stopped a larger request). Also covers a Workday board that exhausts the page ceiling and an Apple search whose scroll cap kept yielding new rows. The run summary prints a `Saturated` warning line per affected source, so incomplete coverage is never silent.
- **No description, no score.** A row with no obtainable description (e.g. a signup-walled LinkedIn posting) is left un-scored — blank Fit and blank Notes — rather than guessed at, and the count is called out in the run summary. To retry, delete the row from `jobs.csv`; the next `jobs run` re-scrapes it fresh. `jobs backfill` cannot recover these rows since it never re-scrapes.

**Resilience.** `jobs run` writes as it works, so an interrupt keeps what finished:

- Each source appends as it completes. LinkedIn and Indeed (the multi-hour sources) checkpoint after every finished search unit (one search term × country), and the board-backed sources (Greenhouse, Ashby, Lever, Workable, Workday) checkpoint after every finished board — so a crash deep into a multi-hour scrape or a discovery-scale board walk loses at most the one in-flight unit. Fast single-call sources (RemoteOK, the HN sources, WeWorkRemotely, Apple) append once on finish; their loss window is seconds.
- Enrichment rewrites the file after each phase and periodically within the long LLM phases.
- Ctrl-C or a scheduled `SIGTERM` drains gracefully: each running source finishes its current unit, its rows are deduped and appended, and the run exits. An interrupt during scraping skips enrichment (fill with `jobs backfill`); during enrichment it loses at most the last save window. A second Ctrl-C quits immediately.
- Exit codes: `0` when every source succeeded and saves were clean; `1` when any source failed or persistence degraded (even if the final save recovered the data); `130` for `SIGINT`, `143` for `SIGTERM`.

**Sources.** `apple`, `ashby`, `greenhouse`, `hn_jobs`, `hn_who_is_hiring`, `indeed`, `lever`, `linkedin`, `remoteok`, `weworkremotely`, `workable`, `workday`. `linkedin` and `indeed` are site-named selectors (`-S linkedin` or `-S linkedin,indeed`); each enabled site is fetched separately, under its own progress row, retry, and failure isolation. `daily-driver help sources` lists them at runtime.

**Live display.** On a TTY, `jobs run` pins a display at the bottom: a `Scraping sources` header bar (green ok / red failed) with one bar per source, then an `Enriching jobs` group. Details covered once in [usage.md](usage.md#output-and-verbosity); the short version: each source reports against its natural unit, stays pinned at its result, recolours into a found/new/already-in-csv/skipped breakdown on finish, and the run ends with a reconciling `Completed:` line. `-q` suppresses the display; `-v`/`-vv` only change how much scrolls above it. Non-interactive output (cron, launchd, pipes) falls back to plain lines.

> **VS Code integrated terminal**: a frozen copy of the live block left in scrollback is VS Code's DOM renderer mishandling scroll-region repaints, not the program. Set `terminal.integrated.gpuAcceleration` to `"auto"` (or `"on"`) to fix it. iTerm2 and tmux are unaffected.

### `jobs backfill [-n|--dry-run] [--limit N] [--force-update] [--cooldown-hours N] [-j|--json]`

Re-enriches empty fields (Fit, Notes) on existing rows without scraping. Shares the `jobs run` enrichment driver: a `Job backfill` live progress block, the fit/notes coordinator under one concurrency cap, periodic saves every ~25 results, and the ollama reachability preflight.

- Only rows in the active funnel (`found` or `pending`) are ever (re-)enriched; a triaged status (`applied`, `interviewing`, `rejected`, `dropped`, `closed`, `skipped`) or a blank status is always left untouched. Fit and Notes are one combined need, since a single call fills both.
- `-n` / `--dry-run` — would-enrich counts, no LLM calls, no writes.
- `--limit N` — cap LLM spend by bounding the fit/notes budget at `N` (minimum 1; default: the configured per-phase cap).
- `--force-update` — re-enrich every eligible row and OVERWRITE its existing Fit, Notes, and Remote, instead of the default fill-missing-only behavior. Still bounded by `--limit` and the cooldown below.
- `--cooldown-hours N` — only meaningful with `--force-update`: skip rows re-enriched within the last `N` hours, so an interrupted force-update resumes instead of restarting from the first row. Defaults to the config `plugins.job_search.enrichment.force_recook_cooldown_hours` (normally 24); `0` disables the cooldown (re-enrich every eligible row every time).
- `-j` / `--json` — emit the completion summary (`rows`, `skipped`, `needs_before`, `needs_after`, `enriched`, `elapsed_seconds`; `dry_run` adds `fit_cap`) in the `{schema, data}` envelope, with the live progress block suppressed.
- A pre-mutation backup lands under `backups/` only when a write actually happens, so a no-op backfill (e.g. ollama is down) leaves `jobs.csv` untouched and writes no backup.
- Ctrl-C or `SIGTERM` saves partial progress and names the backup (`130` / `143`).

Unlike `jobs run`, backfill holds the jobs lock across its whole read -> enrich -> rewrite window — it rewrites from an in-memory snapshot, so dropping the lock mid-enrichment would let a concurrent run's appended rows be clobbered.

### `jobs promote URL-OR-COMPANY [-n|--dry-run]`

Promotes a `jobs.csv` row into a tracker `job` entry once it needs active driving (interviews, follow-ups). The tracker drives work (follow-ups, day-start planning); `jobs.csv` is the discovery / triage record; promotion is the explicit, never-automatic bridge.

- **Selector**, resolved in order: exact match on the row's Link URL (primary), then an unambiguous case-insensitive Company substring. No match or more than one lists candidates and exits `1` — pass the full Link URL to disambiguate.
- **New entry**: category `job`, titled `<Company> -- <Role>`, with the job URL as both `link` and an `extras` key (with `company`, `role`, `source`). The success line names the status (`Promoted job-001 [applied]: ...`).
- **Status** carries through from the row when it is a recognized job status; a blank or out-of-set status falls back to `applied`, with a one-line warning naming the substitution.
- **Idempotent**: the `extras` URL is the durable key, so promoting the same URL twice reports `already promoted as <id>` and exits `0`. A row with no Link falls back to a weaker `(company, role)` key, noted in the success line as `(row has no Link)`.
- **Never mutates `jobs.csv`** — `promote` only writes the tracker. `-n` / `--dry-run` prints the entry that would be created and writes nothing.

### `jobs discover-boards [--full] [-j|--json]`

Sweeps the ATS slug universe — every known Greenhouse, Ashby, and Lever board slug, seeded from the job-board-aggregator project's published lists (~8.3k + ~3.1k + ~4.4k slugs, refreshed daily upstream and cached locally under the workspace state dir for offline reuse) — probing each board's public titles listing and recording boards with at least one role-matching title in a per-platform sweep cache. Matched boards feed `jobs run` automatically: each run scrapes the union of the matched cache and your hand-pinned `*_boards` config entries, minus the per-platform `exclude_boards` blocklist — so discovery never requires a config edit, and a noisy or broken board is silenced by excluding it rather than curating the whole universe.

- **On-demand and long-running.** A first full sweep probes thousands of boards at per-platform worker caps (greenhouse 30, ashby 5, lever 30) with jittered pacing and takes tens of minutes; that is why it is a separate command, not part of `jobs run`. Ashby rate-limits aggressively, so a heavily rate-limited Ashby pass (5 workers, up to 30s backoff per retry) can stretch well past that estimate — affected slugs surface as transient failures and retry next sweep. Later sweeps are incremental by default: only slugs never probed before (new upstream additions, and transient failures from earlier sweeps) are fetched.
- **`--full`** re-probes every known non-dead slug, so a board that stopped listing matching roles drops out of the cache. Rows already in `jobs.csv` are untouched either way — board-diff closure only operates on boards actually enumerated by a `jobs run`.
- **Dead-slug cache.** A board answering HTTP 404/410 (or Ashby's unknown-org response) is recorded as permanently dead and never probed again. Rate limits, timeouts, and server errors are NEVER cached as dead — those slugs simply retry on the next sweep and are reported as transient failures.
- **Interruption-safe.** Completed probes flush periodically and on Ctrl-C/SIGTERM; rerunning continues where the sweep stopped.
- **`--json`** emits the per-platform sweep summary (universe size and source, candidates, probed, newly matched, total matched, newly dead, transient failures) in the `{schema, data}` envelope.

### `jobs status [--json]`

Reads `jobs-last-run.json` and `jobs.csv` metadata. When the last run was cut short, it prints a recovery line naming the phase it reached and pointing at `jobs backfill` to finish enrichment. Below the per-status table it prints the cumulative unscored backlog — active (`found`/`pending`) rows with no `Date Enriched` yet — so a fit budget that cannot keep up with inflow is visible before the review queue starves (`--json` key: `unscored_backlog`). The count includes rows with no cached description, which neither a bigger fit budget nor `jobs backfill` can score (only a re-scrape can); it is therefore an upper bound on the run summary's `Backlog: N remaining`. When a discovery sweep has run, a `Discovered boards` section shows per-platform matched-board counts, slugs swept, and the last sweep date (`--json` key: `discovery`).

### `jobs verify [--reverify-days N] [--unverified-age-days N] [-S|--sources LIST] [--limit N] [-n|--dry-run] [-j|--json]`

URL-checks stale untriaged rows from the sources board-diff cannot cover — LinkedIn, Apple, We Work Remotely, RemoteOK, and HN Jobs rows with real external URLs (board-backed rows are re-verified for free by each run's full listings). A row is due when its last affirmative liveness evidence (`Date Verified`, falling back to `Date Found`) is at least `--reverify-days` old (default: `plugins.job_search.verify.reverify_days`, 7). Each source has its own closed detector: a clean 404 or an explicit closed marker ("No longer accepting applications", "This job post is closed", a delisting redirect, Apple's job-details API 404) closes the row; anything ambiguous — bot walls, timeouts, unexpected statuses — is `unknown` and never closes. Live rows refresh `Date Verified`; closed rows get `Status: closed` + `Date Closed` + a Notes annotation and later archive via `jobs prune`. If every checked row of a source reads closed across a real sample (10+), the source is flagged suspect and none of its closures apply — a page redesign must not mass-close live jobs; the discarded rows are listed in the output and the manifest for a by-hand check.

Rows with **no URL to check** — Indeed (bot-walled) and HN Who's Hiring (comment permalinks never 404), plus HN Jobs rows whose URL fell back to an HN permalink — close by age instead: once their last affirmative liveness evidence (`Date Verified`, which scrape re-sightings keep refreshing, else `Date Found`) is `--unverified-age-days` old (default: `verify.unverified_age_days`, 30) they close as `age-unverified`, a label honest about the weak evidence. A row a recent run re-sighted is never age-closed, and like every verification closure, an age-closed job a scrape re-finds reopens loudly.

`-S|--sources` takes a comma-separated list (same shape as `jobs run -S`) validated against the verifiable sources (exit 2 on anything else; board-backed sources are covered by `jobs run`, not here). `--limit N` bounds the URL probes only, spending them on the stalest evidence first — age closures need no probe and are not capped (preview the full set with `--dry-run`). The report lands in `jobs-last-verify.json` (self-describing: it records the `reverify_days`/`unverified_age_days`/`limit`/`sources` that shaped the run) and surfaces in `jobs status`, including a suspect-detector warning. Requests pace per host with `enrichment.detail_delay_seconds`.

### `jobs prune --older-than SPEC [--status STATUS]... [-n|--dry-run] [-j|--json]`

Moves stale rows from `jobs.csv` to `jobs.archive.csv`. Archived rows suppress re-discovery by WHY they left: user-triaged rows (dropped/rejected/skipped) never return (URL blocked forever; company+role blocked for 45 days so a re-posted role eventually resurfaces), while verification-closed rows (`Status: closed`) stay re-discoverable — a re-scraped closed job is re-added and announced as `Reopened`. Staleness ages from the `Date Verified` column — the last date the job was affirmatively confirmed live (a scrape re-sighting, presence in a board's full listing, or a `jobs verify` URL check) — falling back to `Date Found` for rows never confirmed since discovery. `--older-than` is required and accepts the same grammar as `tracker prune --older-than`. `--status` is repeatable; default targets are `dropped`, `rejected`, `closed`. To prune stale in-progress rows, pass the real statuses, e.g. `--status applied --status interviewing`. `--json` emits the candidate/archived set (`dry_run`, `candidates`, `archived`) in the `{schema, data}` envelope.

**Schema note:** `Date Verified` replaced the pre-1.0 `Date Last Seen` column, and a `Date Closed` column (written by board-diff closure and `jobs verify`) was added. There is no automatic migration — an old-schema file should be fixed by hand (rename the `Date Last Seen` header cell to `Date Verified`, append an empty `Date Closed` column) or the workspace restarted fresh.

## Scheduler (macOS)

`scheduler {install,uninstall,status}` manages launchd plists.

### `scheduler install`

Renders launchd plists into `~/Library/LaunchAgents/` and `launchctl load`s them. Reads `scheduler:` from `.dd-config.yaml` (freeform dict passed to the Jinja template). Defaults: check-in at 11:00 and 15:00, jobs at 07:00, day-cycle at `schedule.day_start` / `schedule.day_end` (configurable in `.dd-config.yaml`). Idempotent.

### `scheduler uninstall`

`launchctl unload` + delete plist. State mirror under `.daily-driver/state/launchd/` is always removed.

### `scheduler status [--json]`

Lists configured jobs and whether each plist is currently installed in `~/Library/LaunchAgents/`.

## Calendar (macOS)

### `calendar sync`

Writes today's plan time blocks into a local macOS Calendar so the day's agenda shows up alongside the rest of your calendar. The source is the day's plan: each `plan_items[]` entry whose `time_block` is an `HH:MM-HH:MM` range becomes one calendar event.

- **macOS-only and opt-in.** Sync is a clean no-op unless `calendar.sync_enabled: true` is set in `.dd-config.yaml` (see [configuration.md](configuration.md#calendar)). Events are written to the calendar named by `calendar.plan_calendar_name` (default `Daily Plan`), which must already exist in Calendar.app.
- **Idempotent.** Each event is tagged `daily-plan:<date>`; re-running for the same day removes that day's existing tagged events and rewrites them, so editing the plan and syncing again replaces — never duplicates — the day's blocks.
- **Best-effort.** A missing calendar, a non-macOS host, or a denied permission prompt degrades to a clean no-op (exit `0`) with a one-line stderr notice — it never aborts the day-start flow.

## Scripting helpers

### `paths <kind> [--date YYYY-MM-DD] [--json]`

Prints a resolved workspace path. Kinds: `root`, `output`, `state`, `ephemeral`, `tracker`, `daily`, `daily-plan`, `daily-notes`, `daily-state`. `--json` emits all paths at once, including the `tracker` key (`tracker.yaml`).

### `gather {calendar,git} [--since|--until|--json]`

Structured external state readers. `gather git` also accepts `--repo PATH`; without it, repos are discovered under `gather.git.search_paths` from `.dd-config.yaml` (falling back to the current working directory).

## See also

- [usage.md](usage.md) — end-user walkthrough.
- [configuration.md](configuration.md) — `.dd-config.yaml` reference.
- [cli-tree.md](cli-tree.md) — at-a-glance command tree.
