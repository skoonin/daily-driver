# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed — two-stream console output + repeatable verbosity

- **Centralized stderr/stdout routing via `core.console.Console`.** All
  CLI status, warning, and error output flows through
  `Console.info/success/warning/error` on stderr; data payloads stay on
  stdout. Module loggers continue to use stdlib `logging`; their
  RichHandler shares `Console.get_log_console()` for color consistency.
- **`-v` is now repeatable.** `-v` enables INFO logging;  `-vv`
  enables DEBUG logging and `Console.debug`. `--quiet` shows errors
  only. Previous behavior (single `-v` flipping straight to DEBUG) is
  gone; existing `-v` callers see INFO instead.
- **`--no-color` decoupled from markup.** `color_system=None` handles
  color suppression; Rich markup and highlight remain enabled so
  structured output (tables, styled prefixes) still renders cleanly in
  no-color mode.

### Added — shared jobs lock + cancel-save for backfill

- **Shared sentinel lock (`.jobs.lock`)** serializes `jobs run`, `jobs
  prune`, and `jobs run --backfill` mutations on a sibling lockfile via
  `core.jobs_lock.jobs_lock_path`, so the three writers can no longer
  interleave on `jobs.csv`.
- **Ctrl-C during `jobs run --backfill` now persists partial progress.**
  In-memory enrichment state up to the interrupt point is flushed via
  atomic `.csv.tmp` + rename. A single timestamped `jobs.csv.bak.<unix>`
  snapshot of the pre-enrichment CSV is taken at the start of each
  backfill run; the interrupt message names the backup file so users
  can recover. If the partial-save itself fails (disk full, etc.) the
  message says so and still names the `.bak` file for rollback. CLI
  exits 130.

### Added — parallel Ollama enrichment

- **`ai.ollama.max_parallel` fans out enrichment across a thread pool.**
  Default 4, matching Ollama's server-side `OLLAMA_NUM_PARALLEL`. Applies
  to both `jobs run` and `jobs run --backfill` — anywhere
  `enrich_company_descriptions` or `enrich_fit_and_notes` runs under the
  ollama provider. Cuts a ~100-row backfill from minutes to under a
  minute on a warm model. Set to 1 to force serial behavior. Claude path
  is unchanged — subprocess fan-out is not worth the blast radius.
- **Builds on the cancel-save guarantee above.** Completed worker
  results are stitched into `jobs.csv` before the interrupt propagates,
  so partial progress survives Ctrl-C. A SIGINT handler prints an
  immediate user-facing acknowledgment ("Stopping — waiting for N
  companies still being enriched...") so the press is never silent,
  even when workers are mid-call. Second Ctrl-C restores the previous
  signal handler and re-sends SIGINT, force-quitting.
- **Per-worker log tag.** `[enrich w2]` / `[enrich-fit-notes w3]` lets
  interleaved failures be traced to their worker without grepping by
  company name. Only emitted when `max_parallel > 1`.
- **Doctor row shows effective parallelism.** The `AI providers` row
  always appends `parallel=N` when ollama is reachable, so the
  configured value is visible at a glance.
- **New doctor row: `Jobs backups`.** Warns when more than 5
  `jobs.csv.bak.*` files have accumulated next to `jobs.csv` — these
  are produced one per `--backfill` run and pile up silently otherwise.

### Added — Ollama provider for headless AI tasks

- **`ai:` config block routes enrichment and summary to either claude or
  a local Ollama server.** Per-task selection: `ai.enrichment.provider`
  and `ai.summary.provider` accept `claude` (default) or `ollama`. Each
  task has its own `model` field — claude takes `sonnet` / `haiku` /
  etc., ollama takes any pulled tag like `qwen2.5:14b` or `phi4`.
  Omitting the entire block preserves existing claude-only behavior
  (no migration required for existing workspaces).
- **New `ai_provider.invoke_for(task, prompt, ...)` dispatch layer.**
  Translates backend failures into a uniform `AIInvocationError` (or
  `AITimeoutError` subclass for timeouts; same single-type catch suffices)
  with `stdout` / `stderr` / `returncode` / `timeout_seconds` fields, so
  the diagnostic warning in `[enrich]` / `[enrich-fit-notes]` paths
  surfaces real upstream
  error messages for both providers.
- **`ollama_client.generate` HTTP wrapper around `/api/generate`.**
  Non-streaming; `format=json` plumbed through for tasks that expect
  structured output (currently `enrich_fit_and_notes`). No retries —
  local ollama either works or it doesn't.
- **New `AI providers` doctor row when any task is routed to ollama.**
  Hits `GET /api/tags`, verifies the configured model is pulled. Status
  is `OK` when reachable + model present, `WARNING` otherwise (drift
  convention: exit 0). The row is omitted when only claude defaults are
  in use — no extra noise for the common case.
- **Documentation.** `docs/ollama-setup.md` walks through install,
  pulling a model, and wiring up `.dd-config.yaml`. `docs/configuration.md`
  documents the `ai:` block.

Interactive launchers (`day-start`, `check-in`, `day-end`) remain claude-
only — they depend on session resume, agents, and workspace `--add-dir`
context that ollama does not provide.

### Changed — slash command rename: `/interview-prep` → `/daily-learning`

- **Renamed** `commands/daily-driver/interview-prep.md` →
  `daily-learning.md` (frontmatter `name:` updated to match). The command
  is broadening from interview-only practice toward a general daily
  learning drill, and the old name no longer reflects intent.
- **`/day-start` updated** to point users at `/daily-learning` in the
  opt-in practice prompt. Users invoking the old `/interview-prep` slash
  will get nothing — there is no alias.

### Fixed — enrichment-path 403/429 errors

- **Skip HN detail enrichment.** `news.ycombinator.com/item?id=*` URLs no
  longer trigger an HTTP fetch — HN aggressively 429s on bursts and the
  title-derived data from `hn_who_is_hiring` / `hn_jobs` is sufficient.
  Logged once per run, not per URL.
- **Skip Indeed detail enrichment.** URLs containing `indeed.com` are
  skipped (mirrors the existing LinkedIn skip): JobSpy already populates
  description, and bare `requests` get bot-walled with 403. Logged once
  per run.
- **Route remaining detail fetches through `_api_get`.** Replaces the bare
  `requests.get` in `enrich_job_details` with the hardened helper added in
  the HN-retry work, so all detail fetches inherit retry / Retry-After /
  exponential backoff. A single `requests.Session` is built once per run
  and reused.

### Fixed — `daily-driver jobs run` UX

- **Verbose mode now names sources, not just counts.** Phase summary lines
  list the source IDs being run (e.g. `[phase1] running 3 headless scrapers
  (remoteok, greenhouse, jobspy), 2 workers`), and `_run_one` emits a
  `[<source>] starting` line at INFO before the scraper executes — so under
  `-v` the user can always tell which scraper is in flight.
- **Single Ctrl-C exits cleanly.** `run_all_scrapers` now catches
  `KeyboardInterrupt` around the phase-1 `ThreadPoolExecutor`, calls
  `pool.shutdown(wait=False, cancel_futures=True)` to drop pending sources,
  and re-raises. The CLI boundary (`_run_scrape`) catches the re-raise,
  prints a clean message to stderr (`interrupted; cancelling pending
  sources...`), and exits with status 130. No traceback. In-flight HTTP
  requests still run to their per-source `timeout` before worker threads
  exit — `requests` cannot be cancelled mid-call.

### Fixed — W9 shipped-prompt schema drift

- `source/daily_driver/agents/daily-driver/work-planner.md`: rewritten to reflect the current category-driven tracker. Replaced `app-NNN` with `{category}-NNN` (e.g. `task-001`, `job-007`) and the `app_id` field with `tracker_id`; generalized planning prose from job-search-only to any tracked work; tracker-update example now uses `<tracker-id>` instead of a bare `<ID>`. (#47)
- `source/daily_driver/commands/daily-driver/{check-in,day-end,day-start}.md`: same drift closed inline — `app-NNN - Company | Role` references replaced with `<tracker-id> - <label>` patterns that match the current tracker schema.

### Breaking — W8 CLI structure & copy polish

- **`core/materialize.py` → `core/generate.py`** (review-2026-04-23 #17). The
  module is renamed; the function `materialize()` is now `generate()` and
  returns a `GenerationResult(n_written, n_preserved)` (or `None` when the
  drift fast-path skips work). The ephemeral lock file at
  `<workspace>/.daily-driver/state/generate.lock` replaces the prior
  `materialize.lock`. CHANGELOG entries from earlier releases keep their
  original wording (historical record). No user action required — internal
  rename; old `materialize.lock` files are harmless and self-cleaning.
- **`install-scheduler` / `uninstall-scheduler` → `scheduler {install,uninstall,status}`**
  (review #26). The two top-level subcommands collapse into a single
  `scheduler` parent. `scheduler status [--json]` is new and lists
  configured jobs from `.dd-config.yaml` plus whether each plist is
  currently installed in `~/Library/LaunchAgents/`. **Migration**: `daily-driver
  install-scheduler` → `daily-driver scheduler install`, `daily-driver
  uninstall-scheduler` → `daily-driver scheduler uninstall`. No alias,
  per pre-1.0 no-compat policy.

### Added — W8 CLI structure & copy polish

- `daily-driver tracker delete <id>` and `daily-driver tracker prune
  [--category CAT] [--status STATUS] [--older-than SPEC] [--dry-run]`
  (#30). `prune` requires at least one filter and refuses to run otherwise
  — pruning everything is too easy to typo. `--older-than` accepts the
  full `parse_since` grammar (`today`, `Nd`, `YYYY-MM-DD`, etc.).
- `daily-driver scheduler status [--json]` (#26): new action that lists
  configured jobs and their installation state.
- `focus.default_duration: '25m'` config field (#19). `daily-driver focus
  on` now works without `--for`, falling back to this config value (then
  to a built-in 25m if unset).
- `daily-driver status` now distinguishes "unconfigured workspace" from
  "quiet day with nothing to report" (#21). Surfaces a `Setup gaps`
  bullet list above the existing tables (Rich) and a `setup_gaps` field
  in `--json` output. Detected gaps: `context.md` is the bundled
  template, `voice-profile.md` is empty or templated, `gather.git.search_paths`
  is empty, tracker is empty or contains only `test-*` fixtures.
- `daily-driver doctor --fix` prints a one-line action log between the
  before / after tables: `Action: regenerated N file(s) (preserved N
  user-edited)` (#24). Only printed when generation actually ran.
- `HelpfulArgumentParser` in `cli/_common.py`: argparse subclass that
  appends `Run: {prog} --help` below the standard usage/error lines on
  every parse failure (#19 addendum). Wired into `cli.py` via
  `parser_class=` so every nested subparser inherits it.
- `--model` flag is now restricted to `choices=[sonnet, opus, haiku]`
  in `day-start`, `day-end`, `check-in`, `summary`, and `voice-update`
  (#16 addendum). Clarified help text: `Claude model to use.`

### Changed — W8 CLI structure & copy polish

- User-facing CLI help text de-jargoned across every subcommand (#16).
  Sample renames: `Scaffold a new daily-driver workspace` → `Create a
  new daily-driver workspace`; `Job-board scraper: run the scraper or
  inspect its last run` → `Search job boards and manage your jobs.csv`;
  `Read external state` → `Pull data from calendar, git, Claude
  sessions, or notes`; `Generate a period summary (headless via claude,
  or --json bundle)` → `Generate a period summary using Claude (or
  --json for raw data)`. Top-level CLI description broadened beyond
  job search to reflect the tracker's actual scope.
- `tracker stats` column headers: `Dimension / Key / Count` → `Group /
  Value / Count`; dimension labels prettified (`total` → `Total`,
  `by_category` → `By category`, `by_status` → `By status`) (#16
  addendum).
- `paths`, `read`, `gather`, and `ensure-daily-dir` are hidden from
  `daily-driver --help` (#26). They remain fully callable — the shipped
  slash commands (`/day-start`, `/check-in`, `/day-end`, work-planner
  agent) invoke them directly. Implementation filters
  `subparsers._choices_actions` in `cli/cli.py`; the
  `argparse.SUPPRESS`-on-subparser pattern is broken under Py 3.11
  (bpo-22848).
- Several user-visible doc/string updates picked up the
  `materialize` → `generate` rename: `docs/{customization,developer,
  install,quick-start,troubleshooting,extending,commands,cli-tree}.md`,
  `README.md`, `CLAUDE.md`, `core/scheduler.py` error text,
  `templates/scheduler.default.yaml`, `templates/.dd-config.yaml.j2`.

### Breaking — W2 jobs CLI rename

- **CLI subcommand renamed**: `daily-driver scrape-jobs` → `daily-driver jobs`. All forms are renamed: `jobs run [--dry-run | --backfill]`, `jobs status [--json]`, `jobs prune --older-than SPEC ...`. There is no alias; invoking `scrape-jobs` errors with argparse's "invalid choice".
- **YAML key `scheduler.scrape_jobs:` → `scheduler.jobs:`** in `.dd-config.yaml`. Stale keys raise `SchedulerError` at `install-scheduler` time pointing at the new key.
- **YAML key `plugins.job_search.scraper.jobspy:` → `plugins.job_search.scraper.jobs:`**. Pydantic model renamed `JobSpyConfig` → `JobsConfig`. Stale `scraper.jobspy:` keys raise `ValidationError` (extra='forbid').
- **launchd label `com.daily-driver.scrape-jobs` → `com.daily-driver.jobs`** and plist filename `scrape-jobs.plist.j2` → `jobs.plist.j2`. `install-scheduler` and `uninstall-scheduler` now sweep the legacy label automatically — pre-rename plists at `~/Library/LaunchAgents/com.daily-driver.scrape-jobs.plist` are unloaded and removed on the next install or uninstall.
- **Log file paths renamed**: `launchd-scrape-jobs.{out,err}` → `launchd-jobs.{out,err}` under `.daily-driver/state/logs/`. Old log files are not auto-deleted; remove `launchd-scrape-jobs.{out,err}` manually if you want to clean up.
- **Migration**: update your `.dd-config.yaml` (rename both `scheduler.scrape_jobs` and any `scraper.jobspy:` block), then re-run `daily-driver install-scheduler`.

### Added -- daily-state epic (W4)

- `core/daily_state.py`: per-day `DailyState` YAML at `<workspace>/.daily-driver/state/daily/YYYY-MM-DD.yaml` with atomic write + flock. Records `last_day_start_session_id`, `last_day_start_at`, `last_check_in_at`, `plan_summary`, and `late_day`. Parse / schema failures raise `DailyStateError` with the on-disk path baked in so a hand-edited file produces an actionable message instead of a bare traceback.
- `daily-driver paths daily-state` and the `daily_state` key in `paths --json` resolve the per-day state file.
- `daily-driver doctor`: new `Daily-state writable` check probes `<state_dir>/state/daily/` via `tempfile.NamedTemporaryFile` (no leaked dotfiles on the error path).
- `daily-driver day-start`: pre-mints a `uuid4` session id, writes a plan stub at the canonical path (only when absent — never clobbers user/claude edits), records `last_day_start_session_id` / `last_day_start_at` / `late_day` in the day's state, then launches `claude --session-id <uuid> --session-name day-cycle-<date>` so /check-in can resume the same conversation later.
- `daily-driver check-in`: when `claude.resume_check_in: true` is set in `.dd-config.yaml` AND today's state has a `last_day_start_session_id`, /check-in launches with `claude --resume <uuid>`. Resume failures fall back to a fresh session with a stderr warning + logger warning — never silent. `last_check_in_at` is recorded only on rc=0; bookkeeping failures after a successful session are demoted to a stderr warning so they don't mis-report success as failure. New `--no-resume` CLI flag forces a fresh session per invocation.
- `core/config_models.py`: new `ClaudeConfig` (`resume_check_in: bool = False`) and `ScheduleConfig` (`day_start: str | None`, `day_end: str | None`, HH:MM with PyYAML-int auto-coercion so unquoted `07:00` is accepted). Both nested models are `extra="forbid"`.
- `core/scheduler.py`: launchd plists for `com.daily-driver.day-start` and `com.daily-driver.day-end` are emitted from `schedule.day_start` / `schedule.day_end`. Single source of truth: the same config knob drives both `is_late_day` and the plist install (per Q11). `uninstall-scheduler` now also unloads the new labels.
- `claude_cli.spawn_interactive` and `claude_cli.invoke` accept `session_id` and `resume_session_id` (mutually exclusive). New `claude_cli.invoke_capture(prompt, ...) -> (stdout, stderr, rc)` returns structured output without raising on non-zero rc (still raises `ClaudeNotFoundError` / `TimeoutExpired` for setup + hang failures) — used by the /check-in template's background `gather sessions` dispatch path so subagent stderr surfaces verbatim.
- `commands/daily-driver/day-start.md` template: instructs claude to read `late_day` from state and frame the agenda as a late-day partial plan when set; step 8 now writes the finalized plan to the canonical path (`daily-driver paths daily-plan`) directly.
- `commands/daily-driver/check-in.md` template: state-driven resume detection (replaces the unverifiable "if resumed from day-start" assumption); surfaces a "no plan today" notice when state is absent (per review #33); dispatches `gather sessions` to a background subagent with explicit verbatim-stderr error handling and a retry / continue / abort prompt — never silent on subagent failure.

### Changed -- daily-state epic (W4)

- `cli/commands/_claude_session.py`: `handle_launch_exception` widened to surface `DailyStateError`, `ValueError`, and `OSError` as clean `error: ...` lines instead of bare-raise tracebacks.
- **BREAKING**: day-start session-name prefix renamed `day-start-<date>` → `day-cycle-<date>` to make the resumable session legible in the `/resume` picker. Per MVP no-compat policy.

### Added

- `daily-driver jobs run --sources LIST`: comma-separated source IDs to run for this invocation, overriding `scraper.sources` toggles. Validated against the registered scraper set; unknown IDs exit 2.
- `daily-driver jobs run --list-sources`: prints the registered source IDs and exits.
- `daily-driver jobs run --dry-run` now renders matches as a Rich table (Source / Company / Role / Location / URL) with a best-effort UTF-8 mojibake reversal so em-dashes and other non-ASCII glyphs display correctly.
- `cli/_common.py`: `add_global_flags(parser)` registers `-v/-q/--no-color/--workspace` (with `default=argparse.SUPPRESS`) into a "global options" group; every leaf calls it AFTER its local args so globals render at the bottom of `--help`. `daily-driver tracker list -v` (and `--workspace PATH` after the subcommand) now parse correctly — previously a parse error.
- `daily-driver tracker list --since SPEC`: filters by `updated_at >= parse_since(SPEC)`.
- `daily-driver jobs prune --older-than SPEC [--status STATUS] [--dry-run]`: moves stale rows from `jobs.csv` to `jobs.archive.csv` (default statuses: `dropped, rejected, closed`). Scraper now unions URLs and dedup-keys from both files at run start so triaged listings are never re-discovered.
- `jobs.csv` schema: added `Date Last Seen` column (defaults to `Date Found` on insert). Drives `prune --older-than`. The on-rescan upsert path is owned by W5/W6; until that lands, prune effectively ages from first-discovery. Existing CSVs are migrated by the legacy-header migrator on next scrape.
- Time-injection hook: `init` materializes `<workspace>/.claude/hooks/hook-current-time.sh` and wires it into `settings.local.json` under `hooks.UserPromptSubmit`. Injects current local time into Claude context on every user message.
- `core/dates.py`: unified `parse_since` / `parse_range` parser shared by `summary` and `gather`. Grammar: `today|yesterday|tomorrow`, `week|month|quarter|year`, `Nd|Nw|Nm|Ny`, `YYYY-MM-DD`, `YYYY-MM-DD:YYYY-MM-DD`. Month math clamps to last day (`Jan 31 - 1m → Dec 31`).
- CLI short flags: `-f` for `--force` (`init`), `-n` for `--dry-run` (`jobs run`, `voice-update`).
- `make test-quick` target — py311 only, fast inner loop. `make test` now runs the full tox envlist (matches CI).
- `docs/cli-tree.md`: snapshot of the v0.1.x CLI command surface (subcommands, flags, parent-parser inheritance gap). Planning reference for the upcoming `parents=[GLOBAL_PARSER]` migration.
- W6: `plugins.job_search.primary_currency` (`USD`/`CAD`/`EUR`/`GBP`, default `null`). When set, scraped jobs whose parsed comp currency doesn't match are dropped at the scraper write boundary. Unparseable comp passes through. (#48)
- W6: Apple/Wellfound adapters now consult the dedup union (`jobs.csv` + `jobs.archive.csv`) via the orchestrator's `_known_urls` config key and skip already-triaged listings during result iteration (saves dict construction + downstream filter work; pagination scroll itself is unchanged).
- W6: `tests/test_scraper/test_e2e_smoke.py` — opt-in `@pytest.mark.smoke` end-to-end scrape against a real source. Excluded from default runs; invoke with `pytest -m smoke`.

### Changed

- `core/config_models.py`: `ScraperConfig.playwright_delays` is now `dict[str, PlaywrightDelays]` and `sources` is `dict[str, SourceToggle]` (auto-coerces legacy `bool` values via a `field_validator`). Replaces ad-hoc dict access in `scraper/_impl.py`.
- `Makefile`: `TOX := $(shell command -v tox 2>/dev/null || echo .venv/bin/tox)` — `make test*` targets now prefer a tox on `PATH` (e.g., `pipx install tox`) over the worktree's `.venv/bin/tox`. Lets `make test` work in git worktrees without running `make setup` there (which creates a local `.venv` that VS Code may auto-activate over the main repo's interpreter).
- W6: scraper now passes `linkedin_fetch_description=True` to JobSpy. JobSpy 1.1.82+ populates LinkedIn comp + description natively, replacing the vendored HTML parser. `enrich_job_details` skips LinkedIn URLs entirely (no HTTP) — JobSpy is the source of truth for LinkedIn detail data.

### Removed

- **BREAKING**: `daily-driver uninstall-scheduler --keep-state` removed. The state mirror under `.daily-driver/state/launchd/` is now always cleaned up on uninstall. The `keep_state` parameter on `core.scheduler.uninstall_all()` is also gone.
- W6: vendored LinkedIn detail-page HTML/JSON-LD parser (`parse_linkedin_html`, `_clean_linkedin_comp`, `_LINKEDIN_*_RE`) and its tests. Use JobSpy `linkedin_fetch_description=True`. Pin `python-jobspy>=1.1.82`. Stale-jobspy `TypeError` now aborts the run with an upgrade hint instead of being swallowed by the catch-all warning.

### Fixed -- doctor (W11)

- `cli/commands/doctor.py`: running `daily-driver doctor` (or `--fix` / `--reset`) without a discoverable workspace now exits 1 with a clear `error: no workspace at <path> (run 'daily-driver init <path>' to scaffold one)` message instead of silently reporting only dependency checks. Resolves review-2026-04-23 #10.
- `core/contract.py`: dropped `.claude/commands/user` and `.claude/agents/user` from the init-contract check. These dirs are user territory — `materialize` never writes to them, so reporting their absence as a fixable ERROR produced a stuck state where `doctor --fix` could not clear the violation. Resolves review-2026-04-23 #11. Hard break (per MVP no-compat): a workspace missing only the user-territory dirs now passes the contract check. **Behavior change:** the `paths` returned by `contract.check()` no longer include these two entries.

### Fixed

- `gathers/calendar.py`: missing-binary warning now includes install + permission instructions, and a non-zero icalBuddy exit links the user to the new "Calendar (icalBuddy) setup" section in `docs/developer.md` (binary install, Calendar access, minimal plist). (#38)
- `gather git`: when run without `--repo`, now consults `gather.git.search_paths` from `.dd-config.yaml` and walks each root at `maxdepth=4` to discover repositories. Both regular clones and linked worktrees (`.git` file) are recognized. Commits are aggregated across all discovered repos. New module `core/git_discovery.py` houses the walker; new `GatherConfig` / `GatherGitConfig` Pydantic models add the `gather.git.search_paths` key. Default behavior unchanged when key is unset (uses `cwd`). (#40)
- `gather notes`: empty-result message now reports the scanned output root, and verbose mode logs the scan path. Helps diagnose `(no notes in window)` when the workspace `output_dir` is misconfigured. (#23)
- `gather` (calendar/git/sessions/notes): in verbose mode (`-v`) the resolved query window is now logged at DEBUG by each gatherer via the new `core.logging.log_query_window` helper. Helps diagnose empty-result false negatives where the bug is in window math rather than data extraction. (#22)
- `gathers/sessions.py`: rewrote to walk `~/.claude/projects/*/*.jsonl` (Claude Code's actual on-disk session format) instead of reading the non-existent `~/.claude/sessions-index.json`. Session id is derived from the filename stem; `started_at` and `cwd` come from the first JSONL line carrying each field. Fixes #7 (false-negative empty results when sessions exist on disk).
- `gathers/calendar.py`: corrected icalBuddy invocation — `to:DATE` is now a single argument (was split into two, producing a usage banner). Added a usage-text guard that detects `USAGE:` / man-page markers in stdout and returns `[]` with a clear log warning instead of attempting to parse the help text as events.

### Changed -- developer tooling (port from coregen-sk)

- `make setup` now delegates to `.ci-tools/setup-venv.sh` via a touchfile sentinel (`.venv/touchfile: pyproject.toml`). The script invokes `.venv/bin/pip` directly, bypassing PEP 668 errors on Homebrew system Python that previously broke a fresh `make setup`.
- `makefiles/setup.mk`: split `setup` (basic) from `setup-dev` (basic + pre-commit hooks); added `setup-force`, `deps-update`, `clean-venv`, `status` targets.
- `makefiles/install.mk`: new `make install` target performs `pip install --user git+file://<repo>` for daily personal use, distinct from the editable `pip-install` dev target. Refuses to run inside an active venv; resolves host Python via `.ci-tools/detect-python.sh` to avoid PEP 668.
- `.pre-commit-config.yaml`: added `check-json`, `check-docstring-first`, `debug-statements`, `check-merge-conflict`, `check-case-conflict`, `autoflake` (imports only), and `pyupgrade --py311-plus` hooks. `tracker.py` is excluded from pyupgrade because its `Tracker.list` method intentionally shadows the builtin under `from __future__ import annotations`.
- Source/test files: pyupgrade auto-modernized typing syntax across 13 files (`Optional[X]` → `X | None`, `List[X]` → `list[X]`, `datetime.timezone.utc` → `datetime.UTC`, etc.). Semantics-preserving on Python 3.11+.
- `.gitignore`: appended sections for test/coverage artifacts, environment files, packaging output, and editor noise.
- `pyproject.toml`: removed orphaned `[tool.bandit]` block (no bandit dep, hook, or invocation references it).

### Fixed -- robustness review follow-ups

- `core/voice.py`: `apply_update` now refuses empty/whitespace-only content (raises `VoiceUpdateError`) so a failed `claude` call cannot blank the profile. Write is atomic via same-directory tempfile + `os.replace`; a mid-write crash leaves the original intact.
- `cli/commands/voice_update.py`: catches `VoiceUpdateError` and exits 1 with a clear stderr message instead of crashing.
- `core/scheduler.py` + `integrations/launchd.py`: `launchctl load` failures now raise typed `LaunchdLoadError` (carrying label, exit code, stderr) and `install_all` re-raises as `SchedulerError`. Previously surfaced as a bare `CalledProcessError`.
- `gathers/calendar.py` + `gathers/git.py`: 30s timeouts on `icalbuddy` / `git log` subprocesses. On timeout, log a warning and return an empty list rather than hanging.
- `core/scraper_status.py`: `load_last_run` validates the parsed JSON is a dict before returning; malformed manifests no longer leak non-dict types to callers.
- `cli/commands/status.py`: route current time through `core.clock.now()` so freezegun-based tests can drive stalled-detection deterministically.

### Changed -- internal typing + tooling

- `cli/cli.py`: command modules typed via a `_CommandModule` Protocol; replaces `dict[str, object]`.
- `core/workspace.py`: split discovery branches into a typed `root_path` to satisfy mypy with no behavior change.
- `pyproject.toml`: scoped mypy `ignore_errors` for `daily_driver.scraper._impl` (vendored module).
- `tox.ini`: invoke mypy as `{envpython} -m mypy` so the tox-managed interpreter is used.
- `docs/developer.md` + CI workflow comments: drop stale references (bandit in lint, "Phase 0" install-smoke note).
- `pyproject.toml`: scoped `warn_return_any = false` for `daily_driver.cli.commands.*` to silence stdlib-stub friction (argparse's `add_parser` and `Namespace` attributes return `Any`).
- `cli/commands/doctor.py`, `cli/commands/_claude_session.py`, `cli/commands/focus.py`: filled in missing parameter/return type annotations to satisfy `disallow_untyped_defs`. `tox -e type` now passes.

### Fixed -- materialize: separate ignore_drift from force_overwrite

- `core/materialize.py`: split the overloaded `force` parameter into two orthogonal booleans — `ignore_drift` (skip the version-stamp fast-path) and `force_overwrite` (overwrite user-edited package-managed files). Previously both concerns shared one flag, making it impossible to re-materialize without also clobbering user edits.
- `doctor --fix` now calls `materialize(ignore_drift=True, force_overwrite=False)`: always runs the materialize body (to repair contract violations even when the stamp is current) but preserves user edits to package-managed `.md` files.
- `doctor --reset` calls `materialize(ignore_drift=True, force_overwrite=True)`: unconditional full overwrite — the nuclear option.
- `materialize` (no args / default): still the normal drift-respecting, user-edit-preserving path.
- When `force_overwrite=False`, stale files dropped from the package are cleaned up via a targeted `_remove_stale_files` helper that skips any file the user has edited. When `force_overwrite=True`, the subdirs are wiped before copy for a clean slate.
- All call sites updated: `init` (which needs `ignore_drift=True, force_overwrite=True`), `test_materialize_contract.py`, and the `test_core/test_doctor.py` / `test_cli/test_doctor.py` spy assertions.

### Added -- MVP Wave 3: init contract + doctor enforcement

- `core/contract.py`: machine-readable specification of every artifact `daily-driver init` must produce. Validation kinds: `exists_file`, `exists_dir`, `parses_yaml`, `parses_config`, `json_valid`, `count_gte`.
- `doctor` now runs `contract.check()` on the workspace root and surfaces each violation as a per-entry `contract:<path>` ERROR result with `--fix` hint.
- `tests/test_core/test_materialize_contract.py`: regression suite that counts actual files on disk after `materialize(force=True)` — closes the broken-wheel gap that shipped in v0.1.0 (importability passed with zero `.md` files).
- Init contract rationale and entry catalogue documented in `docs/developer.md`.
- Package-data `summary.md` + `voice-update.md` slash commands under `source/daily_driver/commands/daily-driver/`.

### Fixed -- MVP Wave 3 post-review

- `doctor --fix` now re-materializes on `contract:*` ERROR results (previously only matched `name == "Workspace drift"`, silently no-op on contract failures). Round-trip regression test added in `tests/test_core/test_contract.py`.

### Added -- MVP Wave 2b: voice-update + scrape-jobs status

- New `daily-driver voice-update --from PATH... [--append | --replace] [--dry-run] [--no-clipboard]` command. Accepts files and directories (recurses `.md`/`.txt`). Binary and >500KB files silently skipped. `--replace` creates a `voice-profile.md.bak` before overwriting. Write is `file_lock`-guarded.
- `core/voice.py`: `collect_source_files`, `build_prompt`, `apply_update` separate CLI from logic.
- `scrape-jobs` converted to a parent with `run` and `status` subcommands.
  - `daily-driver scrape-jobs run [--dry-run] [--backfill]` — prior flat behavior.
  - `daily-driver scrape-jobs status [--json]` — surfaces `jobs-last-run.json` + `jobs.csv` counts. "Awaiting action" = jobs in `applied` or `interviewing`. Missing manifest emits "never run" and exits 0.
- `core/scraper_status.py`: manifest + CSV aggregation.

### Changed -- MVP Wave 2b

- Launchd scheduler (`core/scheduler.py`) now invokes `daily-driver scrape-jobs run` instead of the flat form.
- `cli/commands/read_.py`: `read voice-profile` warning now references `daily-driver voice-update --from <path>` (was stale `/voice-update` slash command).

### Added -- MVP Wave 2a: summary command, scraper unification, user-safety split, bundled slash commands

- New `daily-driver summary --range <spec> [--detail {low,med,high}] [--match <kw>]... [--json]` command. Single template, no modes; claude reads workspace files in-window and synthesizes. `--json` emits the gathered bundle without claude synthesis. Supersedes legacy `standup`.
- Bundled slash commands shipped as package-data under `source/daily_driver/commands/daily-driver/`: `day-start.md`, `day-end.md`, `check-in.md`. All bodies rewritten to call `daily-driver` subcommands (not legacy `bash scripts/`).
- Bundled `work-planner` agent shipped as package-data at `source/daily_driver/agents/daily-driver/work-planner.md`.
- `JobSearchPlugin` pydantic model extended to absorb every scraper config key (`roles`, `domain_keywords`, `seniority_keywords`, `min_comp_usd`, `scraper` nested ScraperConfig + JobSpyConfig). `extra="forbid"` at plugin level and all nested models.
- `core/manifest.py`: SHA-256 per-file manifest written to `.daily-driver/manifest.json`; used by `materialize` to detect user edits to package-managed files.
- `materialize` preserves user-edited package-managed `.md` files unless called with `force=True` (warns listing the file paths).
- `init` scaffolds `.claude/commands/user/` + `.claude/agents/user/` (user territory, never touched by future materialize).
- New workspace-root `.gitignore` rendered from `templates/gitignore.j2` on init: ignores package-managed dirs + settings.local.json + ephemeral state; explicit `!` overrides for user dirs.

### Changed -- MVP Wave 2a

- `scrape-jobs` now reads `plugins.job_search` from `.dd-config.yaml` via the parsed pydantic Config. `--config` flag removed. Legacy `config.yaml` at workspace root produces a migration error pointing to `docs/usage/config-reference.md`.
- `workspace.py` `_MINIMAL_CONFIG` replaced by a Jinja render of `templates/.dd-config.yaml.j2`.
- `settings.json` → `settings.local.json` throughout (template renamed, materialize path updated). Settings merge logic: preserve user-added top-level keys; refresh package-owned keys on each materialize; `init --force` regenerates.

### Removed -- MVP Wave 2a

- `daily-driver standup` subcommand + `cli/commands/standup.py` + associated tests. Generate a 48h standup via `daily-driver summary --range 48h --detail low`.

### Fixed -- MVP Wave 2a (post-review)

- Removed dead `PlaywrightDelays` pydantic class (unreferenced).
- `summary.py` docstring corrected (no longer falsely claims `zoneinfo` usage — range work is day-granular via `date`).
- `_claude_session.py` docstring no longer references the removed `standup` command.

### Added -- MVP Wave 1: polish + JSON parity + docs reframe

- `--json` output on `tracker list`, `tracker stats`, `tracker follow-ups`, `focus status`, `paths`, `gather calendar|git|sessions|notes`. All query commands now emit `{"schema": 1, "data": <payload>}` envelope. `status --json` also wrapped in the same envelope (was raw payload; now versioned for future field additions).
- `docs/usage/config-reference.md` — full catalogue of every `.dd-config.yaml` key derived from pydantic models with type/default/description/example per field and worked examples at the bottom.
- `docs/usage/tracker.md` — tracker deep-dive with per-subcommand examples and custom-category definition walk-through.
- `docs/developer/architecture/purpose.md` — product framing: Personal assistant; program owns durable record; Claude is conversational layer; jobs is one plugin.
- `docs/developer/architecture/decisions.md`: 8 new entries covering config-driven tracker categories, required-category rationale, pydantic retention, no-DB-for-MVP, plugin extension seam, macOS-only scope, lockfile deferral, and forward-looking `ui.terminal_app` config for any future scheduled interactive commands.

### Changed -- MVP Wave 1

- `CLAUDE.md`: reframed to lead with Personal assistant purpose; command-surface section now lists all 16 shipped subcommands including the previously-omitted `read`, `paths`, `ensure-daily-dir`, `gather`, and `install-scheduler`/`uninstall-scheduler`.
- `docs/usage/README.md`, `docs/usage/commands.md`: reframed as tasks-of-any-kind (jobs is one built-in category example, not the product framing).
- Canonical Claude Code install URL unified across `doctor.py` and `_claude_session.py`: both now point to `https://claude.ai/download`.
- `integrations/launchd.py` defines `LaunchdUnavailableError`; every public function checks darwin at entry and raises this class (previously raised generic `FileNotFoundError`). `core/scheduler.py` no longer duplicates the darwin check.

### Fixed -- MVP Wave 1

- `cli/commands/focus.py`: both `focus on` (write) and `focus off` (unlink) now wrapped in `core.locking.file_lock`. The focus lock was previously unguarded — a launchd-triggered `check-in` and a user-invoked `focus on` could produce a torn write.
- `gathers/git.py`: guards against missing `git` binary via `shutil.which`; logs a warning and returns an empty list rather than raising `FileNotFoundError`.

### Removed -- MVP Wave 1

- Empty `source/daily_driver/plugins/` namespace (no loader shipped; directory restored when plugin loader lands).

### Changed -- docs: restructure to mirror coregen-sk layout

- `docs/` reorganized: `architecture/` moved under `developer/architecture/`; `jobs-pipeline-investigation.md` and `pipeline-build-vs-buy.md` moved under `developer/reference/`. New top-level `docs/README.md` and `docs/developer/README.md` split users from contributors.
- New: `docs/developer/quick-start.md`, `docs/developer/contributing/{coding-standards,pre-commit,release-process,testing-guide,version-management}.md`.
- `docs/usage/` rewritten against the actual v0.1.0 CLI: removed references to the un-shipped `summary` command, corrected `tracker add` flag shape (`--title`/`--category` not positional), removed non-existent `scrape-jobs --sources`, fixed `standup` default timeout to 180s, documented actual `paths <kind>` and `read <what>` positional subcommands, added `install-scheduler` / `uninstall-scheduler [--keep-state]` surface.
- `docs/developer/architecture/` updated against source: corrected `Workspace.discover_or_fail(override=...)` classmethod signature and actual dataclass fields, replaced non-existent `launch_claude_session` with `register_interactive_launcher` / `launch_headless` from `_claude_session`, corrected scraper architecture to the flat `_impl.py` + `SCRAPERS` dict design, added scrape-jobs enrichment and scheduled-invocation data flows, added decisions for `extra="forbid"`, integrations/ subprocess funnel, and the flat scraper layout.
- Root `CLAUDE.md`: corrected the command-surface section to describe the shipped `standup` subcommand instead of the un-shipped `summary` umbrella; removed `docs/developer/contributing/version-management.md` stale example that referenced `daily-driver summary --mode interview`.

### Changed

- Internal: split `scraper/_impl.py` into focused modules (`comp`, `parsing`, `csv_io`, `enrichment`, `runner`, `sources/*`). Public surface unchanged.

## [0.1.0] — 2026-04-21

### Added -- Phase 8.1: `make release` / `make release-push`

- `makefiles/release.mk`: `make release VERSION=X.Y.Z` runs the 7-step flow -- clean-tree check, tag-doesn't-exist check, tox py311+py312, local install-smoke in ephemeral venv, `python -m build`, y/N confirm, CHANGELOG `[Unreleased]` -> `[X.Y.Z] -- YYYY-MM-DD` rewrite, `__version__` bump, `release: vX.Y.Z` commit, annotated `vX.Y.Z` tag using the changelog section as the tag message. `make release-push` is a separate deliberate step that requires HEAD to already be tagged before it will push commit + tag to origin.

### Removed -- Phase 8.3: teardown legacy surface

- Deleted `scripts/` (all bash data-gathering + orchestration scripts + `scrape-jobs.py` -- logic now in `daily_driver.scraper._impl`), `commands/` (legacy slash-command markdown -- now shipped as package-data under `source/daily_driver/commands/daily-driver/`), `agents/` (legacy work-planner -- now shipped under `source/daily_driver/agents/daily-driver/`), `launchd/` (legacy hardcoded plists -- replaced by `source/daily_driver/launchd/*.plist.j2` Jinja templates rendered by `core.scheduler`), root `config.yaml` (superseded by in-workspace `.dd-config.yaml` with pydantic-validated `plugins.job_search:` block), `context.md.example` (template now shipped at `source/daily_driver/templates/context.md`), and `settings.json` / `settings.json.tmpl` (rendered from packaged Jinja template by `core.materialize` on init).
- `Makefile`: removed 14 user-facing targets (day-start, day-end, check-in, focus, standup, week-end, month-end, prep, interview-prep, gather-jobs, scrape-jobs, backfill-jobs, voice-update, launchd-install/start/uninstall, install, uninstall). Only developer targets remain (`help`, `setup`, `deps`, `venv`, `status`, `test`, `test-cov`, `test-unit`, `test-cli`, `test-e2e`, `lint`, `format`, `type`, `pip-install`, `pip-uninstall`, `clean`, `distclean`, `build`, `release`, `release-push`).
- `.pre-commit-config.yaml`: removed the `sync-claude-files` hook that called `make install` (no more repo-resident `commands/`/`agents/` to sync into `.claude/`).
- Deleted 11 legacy test modules that depended on the removed `scripts/scrape-jobs.py` module: `test_anthropic_scraper.py`, `test_config.py`, `test_csv.py`, `test_dedup.py`, `test_enrich.py`, `test_hn_scraper.py`, `test_location_filter.py`, `test_matching.py`, `test_scrapers.py`, plus `tests/conftest.py` and `tests/fixtures.py` which only existed to bootstrap that module. Current suite: 346 passing (coverage for the live CLI surface lives under `tests/test_cli/`, `tests/test_core/`, `tests/test_gathers/`, `tests/test_integrations/`, `tests/test_scraper/`).
- Updated `CLAUDE.md`, `README.md`, and `CONTRIBUTING.md` to reflect CLI-only architecture. Scraper in-source comments and user-facing error strings now reference `.dd-config.yaml` / `plugins.job_search.*` instead of the legacy root `config.yaml`.

### Added -- Phase 8.5 + 8.6: end-user and architecture docs

- `docs/usage/`: 7 files walking a new user from install through first run, commands, customizing, troubleshooting, upgrade (indexed by `README.md`). Covers prereqs (Homebrew vs pyenv, optional Playwright), drift detection and `doctor --fix`/`--reset` semantics, launchd debugging (`plutil -lint`, `launchctl list | grep daily-driver`), and the managed-vs-user-owned `.claude/` zones. Ships in-repo, not in the wheel.
- `docs/architecture/`: 6 files documenting the package layout and layer responsibilities (`module-map.md`), per-invocation data flow including program-vs-Claude surface model (`data-flow.md`), load-bearing design choices (`decisions.md` -- argparse, copy-not-symlink, no migrations, no DB, flock, macOS-only scope), extension walkthroughs for pure-Python and Claude-CLI subcommands (`adding-a-subcommand.md`) and scraper sources (`adding-a-scraper-source.md` -- JobSpy vs native parser tradeoff, fixture conventions), and the forward-compat `plugins/` seam (`extensibility.md`).

### Added -- Phase 5.2: scheduler + LaunchAgent install/uninstall (macOS)

- `daily_driver.core.scheduler`: renders and installs launchd plists for `com.daily-driver.checkin` (multi-time StartCalendarInterval) and `com.daily-driver.scrape-jobs` (single time). Reads `scheduler:` block from `.dd-config.yaml`; falls back to package-shipped `templates/scheduler.default.yaml` (checkin 11:00/15:00, scrape-jobs 07:00). `install_all` is idempotent (unload → write → load each run, picks up env changes); `uninstall_all(keep_state=)` removes plists and optionally retains a mirrored copy under `.daily-driver/state/launchd/`. Plist `StandardOutPath`/`StandardErrorPath` land in `.daily-driver/state/logs/launchd-<job>.{out,err}`. Non-darwin platforms raise `SchedulerError("scheduler install/uninstall is macOS-only in v0.1.0")` rather than silently no-op.
- `daily_driver.integrations.launchd`: thin subprocess wrapper around `launchctl load/unload/list` plus plist path resolution (`~/Library/LaunchAgents/<label>.plist`) and atomic file operations.
- `daily_driver.launchd/{checkin,scrape-jobs}.plist.j2`: Jinja2 templates shipped in-package; `templates/scheduler.default.yaml` ships alongside.
- `daily-driver install-scheduler` / `daily-driver uninstall-scheduler [--keep-state]`: new CLI subcommands. 27 new tests across `test_core/test_scheduler.py` (config merge, job build, plist XML parse, platform guard, launchctl-mocked install/uninstall round trip), `test_integrations/test_launchd.py` (path resolution, file write/remove, launchctl invocation), and `test_cli/test_scheduler_cli.py` (end-to-end CLI dispatch + platform-error path). Full suite: 588 passing.

### Added -- Phase 7.2: scraper filter + parser tests

- `tests/test_scraper/test_filters.py`: 38 new tests covering `comp_meets_threshold` (fails-open on unknown comp, 180k default), `location_matches` (remote + cities + countries + empty-location semantics), `matches_roles` (literal, wildcard, exclusion, tier-2 domain+seniority, tier-2b SRE/Platform standalone), `dedup_key` (case/whitespace normalization), `normalize_job` (remote alias collapse, role-suffix strip, Greenhouse board split, comp parsing, non-mutation), and `_parse_comp` (USD/CAD/ISO precedence, K-shorthand, period extraction, range swap).
- `tests/test_scraper/test_parsers.py`: 15 new tests with small inline HTML / JSON-LD fixtures for `parse_linkedin_html` (compensation__salary div, description, missing comp, sidebar salary-info ignored), `parse_greenhouse_html` (Annual Salary prefix, K-shorthand, ignores unrelated $ amounts), and `parse_jsonld_jobposting` (baseSalary, @graph nesting, malformed JSON tolerance). Full suite: 561 passing.

### Added -- Phase 7.3: materialization parity tests

- `tests/test_core/test_materialize.py`: 3 new tests -- package-data resource importability (catches MANIFEST.in / package-data / `__init__.py` gaps that would break a built wheel); `settings.json` rendering from packaged Jinja template; dropped-command wipe on re-materialize (simulates a version-B release that removes a previously-shipped command, verifies the stale file on disk vanishes rather than silently coexisting with the new snapshot). Full suite: 508 passing.

### Added -- Phase 7.4: doctor CLI tests

- `tests/test_cli/test_doctor.py`: 9 new CLI-level tests for the `doctor` subcommand. Covers plain `doctor` (all-OK exit-0, drift WARNING exit-0, ERROR exit-1, missing-workspace graceful degrade), `doctor --fix` (materialize(force=True) dispatch, no-workspace noop), `doctor --reset` (materialize dispatch, no-workspace exit-1 with clear error), and `--fix`/`--reset` mutual exclusivity. Complements the check-logic coverage already present in `tests/test_core/test_doctor.py`. Full suite: 505 passing.

### Fixed -- Review fixes (multi-agent /sk-review pass)

- `integrations.claude_cli.invoke`: switched from `subprocess.run(timeout=)` to `Popen.communicate(timeout=)` with explicit `.kill()` + `.wait()` on `TimeoutExpired`. Prevents orphaned `claude` subprocesses on headless-invocation timeouts (#42).
- `daily-driver tracker update --note`: appends to existing notes (joined with `\n`) instead of replacing them. Updates history is now preserved (#43).
- `core.tracker.Tracker`: drops manual `output_dir` resolution in favour of `Workspace.output_dir` property. Tracker path now honours `~`-prefixed `output_dir` config values (#44).

### Changed -- Review fixes: CLI structural cleanup

- `cli.cli`: replaced 14 individual `try/except ImportError` blocks with a table-driven registration loop backed by `importlib.import_module`. Removed dead `TYPE_CHECKING` import and stub block. ImportError now propagates as a clear packaging-defect traceback (#45).
- `cli.commands._claude_session`: introduced `register_interactive_launcher()` factory. `day_start`, `day_end`, `check_in` subcommand modules collapse to ~12 lines each and share the full body via the factory, while still registering distinct subcommand entry points (#46).
- `cli.commands.init`: dropped the dead `--skip-browsers` flag (always ignored) and its test fixture references (#48).
- `integrations.__init__`: removed all re-export aliases (`integrations.claude_cli` etc. now imported at their real paths). File contains only a module docstring (#47).

### Changed -- Review fixes: style + consistency

- `cli.commands._utils`: new module with shared `resolve_date(raw: str | None) -> date` helper. `paths`, `read`, `ensure-daily-dir` subcommands now delegate to it instead of each maintaining a local duplicate (#49).
- `gathers.{calendar,git,sessions,notes}`: migrated `Optional[X]` → `X | None` and `List[X]` → `list[X]`; dropped the now-unused `typing` imports (#50).
- `gathers.{calendar,git,sessions}`: 7 `log.warning(f"...")` calls converted to lazy `%`-style formatting (#51).
- Misc cleanup (#52): `_claude_session.py` `raise exc` → bare `raise`; `standup.py` clipboard-copy bare `except: pass` now logs via `log.debug(...)` instead of silently swallowing; deleted phase-reference comments in `core/config_models.py`.

### Added -- Phase 8c slice: nested-claude launchers (day-start / day-end / check-in / standup)

- `daily_driver.integrations.claude_cli`: extended with `spawn_interactive(prompt, *, agent, session_name, add_dirs, model)` -- inherits stdin/stdout/stderr for TTY-attached sessions where the user drives the conversation. Shared `_build_args` helper composes the `claude` argv list for both `invoke` (headless) and `spawn_interactive` paths.
- `daily_driver.cli.commands._claude_session`: internal helpers -- `resolve_workspace`, `require_claude_available`, `default_session_name`, `launch_interactive`, `launch_headless`, `handle_launch_exception`, plus a `SessionError` sentinel for uniform exit-1 handling.
- `daily-driver day-start` / `day-end` / `check-in`: interactive launchers that spawn nested `claude` with `--agent work-planner`, the workspace `--add-dir`, a timestamped `-n` session name, and the matching slash-command prompt (`/day-start`, `/day-end`, `/check-in`). `--agent`, `--model`, `--session-name` all overridable.
- `daily-driver standup`: headless launcher -- invokes `claude -p /standup` with `--agent work-planner --output-format text`, `--timeout` (default 120s), pipes stdout to `pbcopy` via `integrations.clipboard` when available (`--no-clipboard` to suppress). `subprocess.TimeoutExpired` maps to exit 1 with a stderr message.
- Tests: 12 new -- 3 parameterized interactive-launcher happy paths (day-start/day-end/check-in prompt + agent + add_dirs + session-name), override-args coverage (`--agent`/`--model`/`--session-name`), missing-claude exit-1, missing-workspace exit-1, claude-exit-code propagation; 5 standup tests (stdout print, clipboard copy, `--no-clipboard`, `--timeout` → exit-1, missing-claude exit-1). Full suite now 490 passing (3 pre-existing multiprocessing pickling failures unchanged).
- CLI now registers 14 subcommands end-to-end: `init`, `doctor`, `tracker`, `status`, `focus`, `scrape-jobs`, `paths`, `read`, `ensure-daily-dir`, `gather`, `day-start`, `day-end`, `check-in`, `standup`.

### Added -- Phase 8b slice: gather nested subcommand (calendar / git / sessions / notes)

- `daily-driver gather <what>`: thin CLI wrapper over `daily_driver.gathers.*` for downstream Claude-prompt pipelines. Subcommands:
  - `calendar [--since --until --json]` -- macOS icalBuddy events, pretty or JSON.
  - `git [--repo --since --until --json]` -- recent commits in a repo (default: CWD).
  - `sessions [--since --until --json]` -- Claude Code sessions from `~/.claude/sessions-index.json`.
  - `notes [--since --until]` -- note files under `{output_dir}/YYYY/MM/`.
  - All subcommands default date ranges via `core.clock.today()`; invalid dates exit 2. Bare `daily-driver gather` prints usage and exits 2.
- Tests: 12 new -- mocked text/JSON emission, empty-set placeholders, date-default wiring, usage-fallback, output_dir resolution. Full suite 478 passing (3 pre-existing multiprocessing pickling failures unchanged).

### Added -- Phase 8a slice: filesystem-read subcommands (paths / read / ensure-daily-dir)

- `Workspace.output_dir` property: resolves `daily_driver.output_dir` from config. Relative paths resolve against the workspace root; absolute paths pass through; `~` expands to `$HOME`. Replaces scattered `workspace.root / config.output_dir` arithmetic in downstream commands.
- `daily-driver paths <kind> [--date YYYY-MM-DD]`: prints workspace-resolved paths. Kinds: `root`, `output`, `state`, `ephemeral`, `daily` (YYYY/MM dir), `daily-plan`, `daily-notes`. Unknown kind exits 2 via argparse choices.
- `daily-driver read <what> [--date] [--frontmatter]`: prints workspace text files. Subcommands: `context` (fatal if missing; exit 1), `voice-profile` (warn to stderr, exit 0 if missing), `plan` (prints "(no plan found …)" if missing, exit 0; `--frontmatter` extracts only the leading YAML block). Bare `read` prints usage and exits 2. Invalid `--date` exits 2.
- `daily-driver ensure-daily-dir [--date]`: creates `{output_dir}/YYYY/MM` (idempotent) and prints the plan path (`{output_dir}/YYYY/MM/YYYY-MM-DD-plan.md`) for shell pipelines. Replaces `scripts/ensure-daily-dir.sh`.
- Tests: 24 new — 4 `Workspace.output_dir` (default `.`, relative, absolute, `~`), 6 `paths` (output/state/daily-plan/daily-notes/missing-ws/unknown-kind), 9 `read` (context present/missing, voice-profile missing-warn/present, plan missing/present/frontmatter/invalid-date, bare-read usage), 5 `ensure-daily-dir` (create+plan-path, idempotent, explicit date, invalid date, missing workspace). Full suite now 466 passing (3 pre-existing multiprocessing pickling failures unchanged).

### Changed -- Phase 7 slice: user-facing docs (README + CONTRIBUTING)

- `README.md`: rewrote around the Python CLI. New install instructions (`pip install git+...`), commands table covering `init`/`doctor`/`tracker`/`status`/`focus`/`scrape-jobs`, workspace-layout diagram, configuration split (`.dd-config.yaml` for structured config vs legacy scraper `config.yaml`), project-layout map under `source/daily_driver/`, macOS-platform note, and an explicit 0.1.0-alpha status block calling out that the legacy bash + Claude Code harness in `scripts/`/`commands/` remains authoritative until v0.1.0 ships.
- `CONTRIBUTING.md`: added. Covers dev setup (`pip install -e '.[dev]'`, `pre-commit install`, `playwright install chromium`), test invocation + marker reference, style-tool matrix (black/isort/flake8/mypy), commit discipline, subcommand-authoring checklist (add parser, register in deferred-import block, test scaffolding, changelog + README), release workflow, and the `.claude/` working-document convention.

### Added -- Phase 6 slice: scraper port (CLI-accessible)

- `daily_driver.scraper`: ported from `scripts/scrape-jobs.py`. Public API `run(config, output_dir, *, dry_run=False)` and `run_backfill(config, csv_path)` — pure functions that return process-style exit codes without `sys.exit()`. The large `_impl` module preserves the original eight sources (RemoteOK, WeWorkRemotely, HN Who's Hiring, Greenhouse, JobSpy, Wellfound, Apple), CSV header migration, role/location filtering, Claude-CLI enrichment (Product/GD/Fit/Notes), comp-threshold filter, and the run manifest (`jobs-last-run.json`).
- `daily-driver scrape-jobs` CLI subcommand: `--config`, `--dry-run`, `--backfill`. Resolves `output_dir` from `.dd-config.yaml` via `Workspace`, defaults `--config` to `<workspace>/config.yaml`. Prints a clear hint and exits 0 when `job_search.scraper.enabled: false`.
- Tests: 11 new — 4 package-import smoke tests (public-API, `_impl` loads without side effects, disabled-scraper short-circuit, YAML loader), 7 CLI tests (help, missing-workspace, missing-config, disabled-scraper, `--backfill` dispatch, `--dry-run` flag forwarding, default-config path). Full suite now 445 passing.

### Added -- Phase 5 slice: gather modules + integration wrappers

- `daily_driver.gathers`: typed, pure-function readers for external state.
  - `git.gather_commits(repo_root, since, until=None)` → `list[GitCommit]`. `git log` with NUL-separated fields so commit subjects can contain anything. Returns `[]` for non-git dirs, raises `CalledProcessError` on other git errors.
  - `calendar.gather_events(since, until)` → `list[CalendarEvent]`. macOS `icalBuddy` wrapper. Returns `[]` if icalBuddy not on PATH. Per-block defensive parsing — one malformed event block never crashes the gather.
  - `sessions.gather_sessions(since, until=None)` → `list[ClaudeSession]`. Parses `~/.claude/sessions-index.json`; tolerates both list and `{"sessions": [...]}` shapes plus alternate field-name spellings.
  - `notes.gather_note_paths(output_dir, since, until=None)` → sorted `list[Path]`. Globs `YYYY/MM/YYYY-MM-DD-*.md` and filters by filename-date.
- `daily_driver.integrations`: thin subprocess wrappers with no hidden state.
  - `clipboard.{available,copy,paste}` around `pbcopy`/`pbpaste`.
  - `claude_cli.{available,invoke}` around the `claude` CLI; `invoke(prompt, *, agent, session_name, headless, input_text, timeout)` builds the args list deterministically. `ClaudeNotFoundError` raised when `claude` missing on PATH (including race where `which` succeeds but `subprocess.run` raises `FileNotFoundError`).
- Tests: 34 new — 6 git, 4 calendar, 6 sessions, 4 notes, 5 clipboard, 9 claude_cli. All use `monkeypatch` to stub `subprocess.run`/`shutil.which`; no real external commands executed. Full suite 434 passing.

### Added -- Phase 4 slice: focus command + CLI e2e tests

- `daily_driver.cli.commands.focus`: `on --for DURATION [--reason TEXT]` / `off` / `status` subcommands. Duration parser accepts `30m`, `2h`, `1h30m`, bare minutes (`90`); invalid input exits 2 via `argparse.ArgumentTypeError`. Lock state lives at `{workspace.ephemeral_dir}/focus.lock` as JSON (`start_iso`, `end_iso`, `end_epoch`, `reason`). `status` auto-cleans expired locks. `on` overwrites an existing lock without erroring.
- Tests: 14 unit tests for `focus` (on/off/status paths, expired-lock cleanup, duration parsing, argparse exit 2 on bad input); 12 subprocess-based CLI e2e tests in `tests/test_cli/test_e2e.py` covering `--version`, bare invocation, init/doctor, tracker add/list/update/filter/stats (including `--extra K=V` and JSON output), and `status --json` structure. Full suite now 400 passing.
- E2E smoke: `daily-driver init $TMPDIR/x && cd $TMPDIR/x && daily-driver focus on --for 5m --reason test && daily-driver focus status && daily-driver focus off` all exit 0.

### Added -- Phase 3 slice: Tracker core + tracker/status subcommands

- `daily_driver.core.tracker`: generic `TrackerEntry` (pydantic, `extra="forbid"`), `TrackerFile`, `Tracker` facade bound to a `Workspace`. CRUD over `{output_dir}/tracker.yaml` with `clock.now()`-sourced timestamps, monotonic per-category IDs (`{cat}-NNN`), and an exclusive `flock` held across the full read-modify-write cycle in `add`/`update` (prevents lost writes under concurrency). Required-field validation inspects both direct parameters and `extras` so category plugins can satisfy requirements via the extras dict.
- `daily_driver.cli.commands.tracker`: `add / update / list / follow-ups / stats` nested subcommands. Rich table on stdout, `--json` for pipelines. `--tags a,b`, `--extra KEY=VALUE` (repeatable), `--due YYYY-MM-DD`.
- `daily_driver.cli.commands.status`: pure-Python Rich dashboard — tracker totals, stalled entries (>14d non-terminal), last 7 days of activity. `--json` available.
- Tests: 12 core (ID monotonicity, frozen-time round-trip, atomicity, concurrent-write serialization, follow-up filters), 7 CLI tracker, 4 CLI status. Full suite now 374 passing.
- E2E smoke: `daily-driver init $TMPDIR/x && cd $TMPDIR/x && daily-driver tracker add … && daily-driver tracker list && daily-driver status` all exit 0 with Rich table output.

### Added -- Phase 2 CLI framework + init + doctor

- `daily_driver.cli.cli.app(argv)`: argparse entry point with `--version`, `-v/--verbose`, `-q/--quiet` (mutually exclusive), `--no-color`, `--workspace PATH`. Deferred command-module imports tolerate missing subcommands during bootstrap. Bare invocation prints help and exits 2.
- `daily_driver.__main__.main`: wires `app(sys.argv[1:])` to `sys.exit`; powers the `daily-driver` console script.
- `daily_driver.cli.commands.init`: scaffolds `.dd-config.yaml` (from Jinja template), `context.md`, `voice-profile.md`, `.claude/`, and `.daily-driver/`, then materializes package data. `--force` renames the existing config to `.bak`, restores on failure, and removes on success (no TOCTOU window).
- `daily_driver.cli.commands.doctor` + `daily_driver.core.doctor`: Python version, core dep presence, external CLI (`claude`), and workspace drift checks. Rich table rendered to stderr. `--fix` re-materializes on drift; `--reset` forces a clean re-copy. Explicit `--workspace` overrides that don't resolve warn instead of silently degrading.
- `daily_driver.templates/{.dd-config.yaml.j2, context.md, voice-profile.md, settings.json.j2}`: seed content for fresh workspaces.
- `pyproject.toml`: `templates/.*.j2` glob added to `package-data` so the dotfile Jinja template ships with the wheel.
- Smoke test verified end-to-end: `daily-driver --version && daily-driver init $TMPDIR/x && cd $TMPDIR/x && daily-driver doctor` exits 0. 351 tests passing.

### Added -- Phase 1 core modules (daily_driver package refactor)

- `daily_driver.core.clock`: timezone-aware time utilities (`now`, `today`, `iso_week`, `month_bounds`) with `FROZEN_TIME` for test determinism.
- `daily_driver.core.logging`: stdlib-backed logging with a Rich stderr handler, idempotent `configure(verbosity)`, and scoped `get_logger(name)`.
- `daily_driver.core.locking`: `file_lock(path, *, shared, timeout)` context manager wrapping `fcntl.flock` with automatic parent-dir creation and optional timeout.
- `daily_driver.core.config_models`: pydantic v2 models for `.dd-config.yaml` (12 submodels + top-level `Config` with `extra="forbid"` and cross-field validators).
- `daily_driver.core.config`: `load(path) -> Config` using `yaml.safe_load`; empty files fall back to a minimal default config.
- `daily_driver.core.workspace`: `Workspace` dataclass with `discover_or_fail()` (walks up from CWD for `.dd-config.yaml`) and `init()` (scaffolds a new workspace); exposes `ephemeral_dir` for lock/scratch paths separate from durable state.
- `daily_driver.core.version_stamp`: atomic `read`/`write` and `is_drifted` over `{state_dir}/version` (stamp written last on materialize, so crashes leave it stale).
- `daily_driver.core.materialize`: copies `daily_driver.commands/daily-driver/*.md` and `daily_driver.agents/daily-driver/*.md` into `.claude/` and renders `daily_driver.templates/settings.json.j2` into `.claude/settings.json`. Fast-path skips on matching stamp; otherwise acquires `materialize.lock` (double-checked), wipes only daily-driver-owned subdirs, copies atomically, and writes the stamp last. Rejects traversal / hidden entries in package data.
- `tests/test_core/`: 83 unit tests covering all 8 modules (concurrent materialize, stamp-ordering invariant, YAML-injection safety, ISO-week boundaries, lock contention, etc.).
- `pyproject.toml`: `pythonpath = ["source", "tests"]` so the legacy `tests/conftest.py` (which does `from fixtures import ...`) resolves without `--noconftest`.

### Fixed -- Scraper regressions from docs-implementation landing

- `scrape-jobs.py`: Drop `--bare` from both Claude CLI enrichment calls. `--bare` skips keychain auth (only honors `ANTHROPIC_API_KEY`), which caused every enrichment subprocess to exit `rc=1` with empty stderr (0/138 enriched in the first post-landing run).
- `scrape-jobs.py`: NaN-safe coercion for JobSpy DataFrame rows via module-level `_jobspy_str(x, default)`. Previous `row.get(k) or ""` returned float NaN (truthy) for missing cells, crashing `.strip()` in `_format_jobspy_comp`.
- `scrape-jobs.py`: Disable Glassdoor in `scrape_jobspy`. JobSpy's Glassdoor path returns HTTP 400 on every request and internal retries cost ~22 minutes per run.
- `scrape-jobs.py`: Add `--debug` CLI flag for DEBUG-level logging during troubleshooting.

### Changed -- Docs-implementation plan: state YAML builders, JobSpy, HN Algolia, comp threshold

- State-dir YAML builders: new `scripts/build-session-delta.sh`, `scripts/build-pipeline-summary.sh`, `scripts/build-standup.sh` write structured YAML under `{state_dir}/`. Read-side helpers `scripts/read-session-delta.sh`, `scripts/show-pipeline-summary.sh` emit informational message + exit 0 when state is missing (commands treat absence as "nothing to report" rather than an error).
- New `scripts/record-ruled-out.sh` and `scripts/list-ruled-out.sh` track companies/roles ruled out during research so the daily planner can avoid re-suggesting them. `scripts/record-interview-state.sh` captures interview outcomes into state YAML. `scripts/find-session-id.sh` and `scripts/read-voice-profile.sh` consolidate repeated inline shell.
- `scripts/snapshot-tracker.sh` writes a tracker snapshot for cross-command consumption.
- `scrape-jobs.py`: LinkedIn and Indeed scrapers replaced by JobSpy (`python-jobspy`). Single code path handles both via `scrape_jobspy(config)`. ISO country codes mapped to JobSpy's expected country names via `COUNTRY_NAMES`. Glassdoor included only for US searches (JobSpy limitation). `_non_headless_sources(config)` derives Phase 2 sources dynamically from `type: playwright` entries in `SCRAPERS`.
- `scrape-jobs.py`: HN Who's Hiring switched from HTML scrape to Algolia JSON API (`hn.algolia.com/api/v1/search`). Reduces parsing brittleness; `nbHits` guard protects against unbounded responses.
- `scrape-jobs.py`: Comp threshold filter (`comp_meets_threshold`) fails open when comp data is missing or unparseable. Jobs below threshold get `status: skipped` written to `jobs.csv` with a note, so the row persists (pattern data) without polluting the active queue.
- `scrape-jobs.py`: `enrich_fit` and `enrich_notes` merged into single `enrich_fit_and_notes` that returns both fields in one Claude call (halves API cost for enrichment). Fit sentinel changed from 50 (would clamp to 10/10) to 5 (neutral midpoint) when insufficient info to assess.
- `config.yaml`: Removed dead `min_comp_currency_assume` key.
- `Makefile`: `deps` target probes `jobspy` import.
- Tests: Rewrote `test_hn_scraper.py` for Algolia JSON contract. Replaced LinkedIn/Indeed Playwright tests with JobSpy fixtures. Combined `TestEnrichFit`/`TestEnrichNotes` into `TestEnrichFitAndNotes`. Added clamp and skipped-status coverage. 242 tests passing.

### Changed -- jobs.csv column reorder; status vocabulary; audit robustness

- `jobs.csv` canonical column order changed to ergonomic triage layout: `Status, Notes, Company, Location, Role, Fit, Comp, Date Found, Date Applied, Link, Product/Purpose, GD Rating, Source`. High-signal fields first; existing migration logic handles rewriting on next run.
- `scrape-jobs.py`: `CANONICAL_HEADER` updated to new order. All reads/writes use `DictReader`/`DictWriter` so no functional impact.
- `tracker.sh` stats: `cmd_stats` now covers all 10 status values (`found`, `researched`, `applied`, `screening`, `interviewing`, `offer`, `skipped`, `rejected`, `ghosted`, `withdrawn`, `dropped`). Removed stale `researching` bucket. Active count narrowed to `applied+screening+interviewing+offer`. Output grouped into Funnel / Pipeline / Closed sections.
- Status vocabulary: added `screening` (active pipeline) and `dropped` (closed; re-activatable if contacted). `researching` removed — canonical value is `researched`.
- `CLAUDE.md`: columns list and status vocabulary updated to match.
- Tests: `CSV_HEADER` fixture and idempotent migration test updated to new column order.

### Changed -- Config-driven state, context move, unified tasks

### Changed -- Centralize state_dir and schedule in config.yaml

- `config.yaml`: New `state_dir`, `schedule` (day_start, checkin, day_end, gather_jobs times), and `claude` (model, effort, subagent_model) sections. Schedule times are now the source of truth for launchd plists -- edit config.yaml and re-run `make launchd-install`.
- `launchd-install.sh`: Reads schedule times from config.yaml instead of hardcoded plist values. Generates checkin multi-interval XML dynamically. Validates HH:MM format.
- Launchd plists converted to templates with PLACEHOLDER values populated by `launchd-install.sh`.
- Scripts (`check-state-dir.sh`, `checkin-state.sh`, `focus-mode.sh`, `gather-sessions.sh`, `open-session.sh`) use `get-state-dir.sh` instead of hardcoded `~/.local/share/daily-driver`.
- `gather-sessions.sh`: Numeric timestamp comparison (was lexicographic), error handling for jq unclosed-session detection.
- `gather-jobs.sh`: Skip weekends.
- `commit-notes.sh`, `week-range.sh`: Use `%G` (ISO week-numbering year) with `%V` week number.
- `tracker.sh`: Duplicate company+role guard on `add`. Audit uses Python csv module for RFC 4180 compliance. Active status filter includes `found`/`researched`.
- `scrape-jobs.py`: `resolve_output_dir` raises on missing config (no silent default). Timeout default 15s -> 30s.

### Changed -- Move context.md to output_dir, template settings.json

- `context.md` removed from repo; `context.md.example` added as template. Actual `context.md` lives in `{output_dir}/context.md`.
- New `scripts/read-context.sh` reads context from output_dir with actionable error messages.
- `settings.json` replaced by `settings.json.tmpl` with PLACEHOLDER_HOME, PLACEHOLDER_STATE_DIR, PLACEHOLDER_OUTPUT_DIR. `make install` generates `settings.json` via sed. Null-guard rejects missing `output_dir`.
- `init-output-dir.sh` copies `context.md.example` on first setup if target doesn't exist.
- All commands updated: `cat context.md` -> `bash scripts/read-context.sh`.

### Changed -- Unify personal task tracking into plan_items

- Personal tasks now tracked as `plan_items` with `type: personal` instead of separate `personal_tasks` frontmatter. Eliminates `pt-NNN` ID namespace; all items use `pi-NNN`.
- `work-planner.md`: Updated schema, ID rules, carry-forward handling, and examples for unified tracking. Personal items use `recurring: true/false`.
- `gather-carryforward.sh`: Emits `[personal]` tag and `type` field. Removed separate personal_tasks section.
- `day-start.md`: Three-source personal item flow (recurring, carried, ad-hoc).
- `day-end.md`: Personal items carry forward via `carry_forward` entries with `type: personal`.

### Changed -- Consistent logging in open-session.sh

- `open-session.sh`: All operational paths (gates, errors, warnings) use `log_msg()` for dual-write to stderr and state-dir log file. Previously, error paths inside `open_session()` only wrote to stderr.

## [Unreleased] -- Location preferences in config

### Changed -- Location preferences moved from markdown to config.yaml

- `config.yaml`: New `job_search.locations` block with `home_city`, `remote` (bool), `countries` (ISO codes), and `cities` (city strings). Replaces `{output_dir}/location-preferences.md` and `job_search.scraper.countries`. The countries list now drives both scraper search scope and location filtering.
- `config.yaml`: New `job_search.persona` for enrichment prompts. New `job_search.domain_keywords` and `job_search.seniority_keywords` for Tier 2 role matching.
- `scrape-jobs.py`: New `location_matches()` hard-filters jobs before enrichment. Empty/missing locations accepted when `remote: true`. New config helpers: `locations_config()`, `persona()`, `home_city()`, `domain_keywords()`, `seniority_keywords()`.
- `scrape-jobs.py`: `_load_location_tiers()` replaced by `_location_summary()`. `COUNTRY_MAP` expanded with IE, AU, ZA. `COUNTRY_NAMES` uses explicit aliases (no ISO code substring matching). `matches_roles()` reads domain/seniority keywords from config.
- `scrape-jobs.py`: Enrichment prompts (`enrich_fit`, `enrich_notes`) now use `persona()` and `home_city()` from config instead of hardcoded strings.
- Tests: New `test_location_filter.py` (23 tests). Updated `test_enrich.py` to remove markdown file mocks. Updated `test_scrapers.py` for new config path.

## [Unreleased] -- v2.0

### Added -- Pagination for LinkedIn, Indeed, Apple scrapers

- `scrape-jobs.py`: LinkedIn paginates via `&start=N` (25/page), Indeed via `&start=N` (10/page), Apple via scroll-triggered API intercepts. All respect `max_pages` config (default 3). Short-circuits on partial pages or empty results. 2s cooldown between LinkedIn pages to reduce bot-detection risk.
- `config.yaml`: New `job_search.scraper.max_pages` (default 3) and `job_search.scraper.date_window_days` (default 7). `date_window_days` drives LinkedIn `f_TPR=r{days*86400}` and Indeed `fromage={days}` parameters (previously hard-coded to 7).
- Tests: `test_linkedin_paginates_up_to_max_pages`, `test_linkedin_url_uses_config_date_window`, `test_linkedin_default_date_window_is_7_days`, `test_indeed_uses_config_date_window`.

### Fixed -- Enrichment budget bug; backfill for existing rows

- `scrape-jobs.py`: `enrich_company_descriptions` used `break` when budget was hit, which stopped the entire job loop -- even jobs whose company was already cached got skipped. Replaced with `continue` + cache sentinel so cached companies are still applied. Budget defaults raised from 10/5/10 to 50/50/50.
- `scrape-jobs.py`: New `--backfill` CLI flag re-enriches existing `jobs.csv` rows with empty Fit, GD Rating, Product/Purpose, or Notes. Uses `_CSV_TO_DICT`/`_DICT_TO_CSV` mappings and `_row_to_dict()`/`_dict_to_row()` helpers for round-trip fidelity.
- `Makefile`: New `backfill-jobs` target (`make backfill-jobs`).
- Tests: `TestEnrichCompanyBudgetFix` (cached companies applied after budget), `TestBackfillHelpers` (row_to_dict, dict_to_row, backfill writes/preserves).

### Changed -- Switch headless scrapers to shared HTTP helpers; Apple API intercept

- `scrape-jobs.py`: RemoteOK now uses its JSON API via `_api_get` (no browser). WeWorkRemotely switched to RSS XML feed via `_http_session`. HN and Greenhouse use shared `_api_get` helper. All four no longer need Playwright for Phase 1.
- `scrape-jobs.py`: Apple scraper rewritten to intercept `/api/v1/search` POST responses via Playwright's `page.on("response")` handler instead of parsing DOM elements. Captures structured JSON with job IDs, titles, locations, and team names.
- Shared helpers: `_http_session(config)` (requests.Session with configured user-agent + timeout) and `_api_get(session, url, config, label=)` (GET with retry logging).
- Tests: WWR tests rewritten with `_wwr_rss_xml()` + `_mock_response_content()`, HN patches `_api_get`, Apple uses `_apple_mock_page()` with response interception mocks.

### Changed -- Replace Anthropic Playwright scraper with Greenhouse Job Board API

- `scrape-jobs.py`: New `scrape_greenhouse(config)` replaces `scrape_anthropic`. Uses the public Greenhouse Job Board API (`boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`) — no browser, no auth, structured JSON with full descriptions. Completes in <1s vs 30s+ for Playwright. Job descriptions are extracted as `description_text` for the Notes enricher.
- `config.yaml`: `greenhouse: true` replaces `anthropic: true` in scraper sources. New `greenhouse_boards` list (default `[anthropic]`) — add any company using Greenhouse as their ATS (e.g. `stripe`, `hashicorp`).
- Tests rewritten from Playwright mocks to API response mocks (11 tests).

### Added -- Notes auto-summary with tech stack, remote policy, red flags

- `scrape-jobs.py`: New `enrich_notes(jobs, config)` generates a one-line summary per job via Claude CLI. Uses `description_text` from the detail page when available (tech stack, remote policy, red flags); falls back to a role-only prompt otherwise. Budget-capped via `max_enrich_notes` (default 10). Controlled by `enrich_notes` config flag (default true).
- `scrape-jobs.py`: Detail-page parsers (`parse_jsonld_jobposting`, `parse_linkedin_html`, `parse_greenhouse_html`) now extract `description_text` from job descriptions. JSON-LD uses the `description` field; LinkedIn targets `.show-more-less-html__markup` / `.description__text` divs; Greenhouse targets `#content` / `.job-description` divs. Stored as an intermediate field on the job dict (not written to CSV).
- `append_jobs` now writes the `Notes` column from job dicts.
- 14 new tests: description extraction across all 3 parsers (6), enrich_notes behavior (7), CSV Notes write-through (1).

### Added -- Claude CLI enrichment for GD Rating and Fit

- `scrape-jobs.py`: `enrich_company_descriptions` now returns Glassdoor rating alongside Product/Purpose in a single two-line prompt. Ratings stored as decimal (e.g. "4.2") or "unknown". Controlled by `enrich_gd_rating` config flag (default true).
- `scrape-jobs.py`: New `enrich_fit(jobs, config)` scores each job 1-10 for fit using the Claude CLI. Reads `location-preferences.md` from output_dir once per run to build the prompt. Budget-capped via `max_enrich_fit` (default 5 jobs/run). Scores stored as "N/10" format matching existing manual entries.
- `append_jobs` now writes `Fit` and `GD Rating` columns from job dicts (previously always blank).
- Fixed missing `config` argument in `main()` call to `enrich_company_descriptions`.
- 11 new tests in `tests/test_enrich.py` covering GD Rating (enabled/disabled/unknown), Fit (scoring/budget/skip/timeout/disabled/no-claude), and CSV column write-through.

### Fixed -- IC-only filter: reject manager/director/junior/intern titles

- `config.yaml`: Added five exclusion patterns to `job_search.roles`: `!*Manager*`, `!*Director*`, `!*Head of*`, `!Junior *`, `!*Internship*`. Closes the Tier 2b gap where titles like "Junior SRE" and "Senior SRE Manager" previously passed the standalone keyword match. Patterns deliberately avoid `!*Intern*` because `re.search` semantics would false-match "International" and "Internal" (both of which appear in legitimate senior IC titles).
- `scrape-jobs.py`: Updated the Tier 2b rationale comment at `matches_roles` to reflect that junior/manager filtering is now delegated to config exclusions rather than claiming the standalone set is "precise enough" on its own.

### Fixed -- Bare "Site Reliability Engineer" title now matches Tier 2b

- `scrape-jobs.py`: `matches_roles` Tier 2b standalone set extended from `{"sre", "platform engineer"}` to also include `"site reliability engineer"`. The `"sre"` substring check does not hit the spelled-out form, so titles like "Site Reliability Engineer" (no seniority prefix) were silently dropped at the title gate even after LinkedIn URL fixes surfaced them in search results. Verified against the Hiive Vancouver posting that motivated Item 6 of the scraper improvement plan.
- New test in `tests/test_matching.py::TestTier2bStandalone::test_standalone_site_reliability_engineer` as a regression guard.

### Added -- Countries of interest: multi-country search for LinkedIn, Indeed, Apple

- `scrape-jobs.py`: New `COUNTRY_MAP` constant maps ISO 3166-1 alpha-2 country codes to per-scraper URL parameters (`apple_locale`, `linkedin_location`, `indeed_host`). Supports US, CA, GB out of the box; extend by adding a row to `COUNTRY_MAP` and a code to `config.yaml`.
- New helpers: `countries_list(config)` (reads `job_search.scraper.countries`, defaults to `["US", "CA"]`), `country_params(country)` (looks up a code in `COUNTRY_MAP`, warns + returns `{}` for unknowns), `_apple_job_id(href)` (extracts numeric job ID for cross-locale dedup).
- `scrape_apple`, `scrape_linkedin`, `scrape_indeed` rewritten to iterate configured countries. Apple deduplicates cross-locale by numeric job ID. LinkedIn and Indeed deduplicate by URL.
- LinkedIn: dropped `f_WT=2` (remote-only filter) and widened `f_TPR` from 24h to 7d (`r604800`), so daily runs that skip a day recover missed postings, and hybrid/onsite roles now surface for location-based ranking.
- Indeed: dropped `l=Remote` location filter; regional host is now country-driven.
- LinkedIn and Indeed scrapers pause 5 s between country switches to reduce bot-detection risk from rapid host/location pivots.
- `config.yaml`: Added `job_search.scraper.countries: ["US", "CA"]`. Removed dead `careers_url: "https://jobs.apple.com/en-ca/search"` from the Apple company entry. Updated LinkedIn and Indeed `search_url` entries (used only by `gather-jobs.sh` to build the manual review queue) to match the new scraper filter set — removed `location=Canada`, `f_WT=2`, `l=Remote`, and the `ca.indeed.com` host.
- 12 new tests in `tests/test_scrapers.py`: helper coverage (`test_countries_list_*`, `test_country_params_*`, `test_apple_job_id_extraction`) and per-scraper multi-country iteration tests (`TestScrapeAppleMultiCountry`, `TestScrapeLinkedInMultiCountry`, `TestScrapeIndeedMultiCountry`).

### Added -- Wildcard and negation support in role matching

- `scrape-jobs.py`: `matches_roles` now supports `*` wildcards (`"Senior * Engineer"` matches any middle term) and `!`-prefixed exclusions (`"!*Manager*"` rejects any title containing "Manager"). Exclusions short-circuit before Tier 2/2b safety nets, fixing IC-only filtering where titles like "Senior SRE Manager" previously passed via the standalone `sre` keyword path.
- New helpers: `_split_roles`, `_role_pattern`, `_role_matches`.
- `_compress_search_terms`: skips wildcarded roles and exclusions — neither translates to URL search queries.
- `config.yaml`: `roles:` list documented with `*` and `!` syntax.
- 16 new tests in `tests/test_matching.py` covering `TestTier1Wildcard`, `TestNegation`, and `_compress_search_terms` filtering.

### Changed -- jobs.csv column layout: Status promoted to first column, '#' removed

- `jobs.csv` format change: `Status` is now column 0 (was column 11); the `#` row-number column is removed entirely (was column 0). New canonical header: `Status, Company, Product/Purpose, Role, Comp, Location, Fit, GD Rating, Source, Date Found, Date Applied, Link, Notes` (13 columns, down from 14).
- Automatic migration: on first run against a legacy 14-column file, `scrape-jobs.py` rewrites `jobs.csv` in place and writes a timestamped backup (`jobs.csv.bak.<epoch>`) so the change is reversible.
- `scrape-jobs.py`: `load_existing_jobs` return type changed from `tuple[set, set, list, int]` to `tuple[set, set, list]` — `next_num` removed. `append_jobs` signature drops `next_num` parameter.
- `tracker.sh`: audit awk script updated — Status column positional reference changed from `fields[11]` to `fields[1]`.

### Changed -- Parallel job scraping

- `scrape-jobs.py`: `run_all_scrapers` now runs in two phases. Phase 1 (`remoteok`, `weworkremotely`, `hn_who_is_hiring`, `anthropic`, `apple`) runs in parallel via `ThreadPoolExecutor` with headless Chromium. Phase 2 (`linkedin`, `indeed`, `wellfound`) stays serial and non-headless as a bot-detection hedge. Cuts wall-clock on the scheduled `gather-jobs.sh` run without changing CSV output or first-scraper-wins dedup semantics.
- New config knob: `job_search.scraper.parallel_workers` (default `4`) controls Phase 1 concurrency; set to `1` for serial Phase 1 behavior.
- Per-scraper timing logged as `[sid] took X.Xs (N jobs)` on success.
- New helpers: `_config_with_headless`, `_run_one`, `_merge_and_dedup`, plus `NON_HEADLESS_SOURCES` constant.

### Fixed -- Audit follow-up (reliability + simplification)

- `tracker.sh audit`: exact company-name match replaces substring glob; "Stripe" no longer false-matches "Stripe Financial"
- `scrape-jobs.py`: `enrich_company_descriptions` now honors `job_search.scraper.max_enrich_companies` budget (default 10) and 15s timeout; prevents runaway Claude calls
- `scrape-jobs.py`: HN scraper catches `requests.RequestException` and returns `[]` instead of crashing the run
- `gather-sessions.sh`: crash-detection `set -e` escape fixed (jq array-diff replaces piped `while read` subshell)
- `calendar-sync.sh`: surfaces AppleScript errors with Privacy > Calendars hint instead of silently swallowing
- `checkin-state.sh`: state writes go through `write_state` helper with `.tmp + mv` atomic pattern
- Consolidated `commit-{daily-notes,weekly,monthly}.sh` into single `commit-notes.sh {daily|weekly|monthly}`

### Fixed -- LinkedIn loose comp regex

- `_clean_linkedin_comp()` now parses postings where the currency code is a suffix rather than a prefix and no unit is present (e.g., `$144,000\u2014$200,000 CAD`). New `_LINKEDIN_LOOSE_RANGE_RE` runs as a fallback after the strict regex; defaults to `/yr` when no unit is present, per job-board convention. Strict regex also updated to accept en-dash and em-dash separators in addition to ASCII hyphen.
- 9 new tests covering em-dash strict path, all loose-format parametrized cases, strict-wins precedence, and unparseable-returns-empty guard.

### Added -- Greenhouse HTML comp parser

- `parse_greenhouse_html()` — extracts salary from Greenhouse job-board pages that omit JSON-LD (e.g., Anthropic's board at `job-boards.greenhouse.io`). Matches the `Annual Salary: $X - $Y USD` text pattern that Anthropic publishes in plain HTML.
- `_parse_detail_page()` now dispatches `greenhouse.io` hosts to the new parser with JSON-LD fallthrough: JSON-LD wins if present (covers any Greenhouse board that does emit structured data), plain-HTML parser runs otherwise.
- 4 new tests in `tests/test_enrich.py` covering labeled salary extraction, hostname dispatch fallthrough, JSON-LD preference, and no-salary case.

### Added -- Job detail enrichment

- `enrich_job_details()` fetches each new job's detail URL once and populates the `Comp` column in `jobs.csv` (previously empty). Hostname dispatch picks a parser strategy: LinkedIn anonymous pages get an HTML parser reading `.compensation__salary`; all other hosts fall through to a JSON-LD `JobPosting` parser that handles Greenhouse/Lever/Ashby-style ATS boards.
- `parse_jsonld_jobposting()` — pure helper; reads `<script type="application/ld+json">` blocks and extracts comp + `datePosted` from `JobPosting` schema with currency-aware formatting (`CA$130,000–150,000/yr`).
- `parse_linkedin_html()` — pure helper; tolerates `.compensation__salary` variants with single or range values.
- Config: `job_search.scraper.detail_delay_seconds` (default 0.5s) throttles detail-page fetches; URL-level cache prevents refetching within a run.
- 30 new tests in `tests/test_enrich.py` covering the parser strategies, hostname dispatch, cache/delay behavior, and the CSV `Comp` column write path.

### Fixed -- Post-review cleanup

- `enrich_company_descriptions`: narrow `except Exception` to `TimeoutExpired`/`OSError`; log timeout at WARNING not DEBUG; take first non-empty line of stdout instead of raw `.strip()`
- `shutil.which` replaces `subprocess.run(["which", ...])` in both enrichment and notification checks
- `csv_path.as_uri()` replaces manual `f"file://{csv_path}"` in `_notify_new_jobs`
- `append_jobs`: `col()` nested function replaced with dict comprehension; split `open()`/`with` replaced with `with open()`
- `SCRAPERS` type annotation tightened to `Callable[[dict], list[dict]]`
- `Optional[int]` replaced with `int | None`; `from typing import Optional` removed
- `scrape_wellfound` and `scrape_apple` docstrings corrected to match implementation
- Tier 2b seniority exemption comment added explaining why SRE/Platform Engineer bypass the seniority gate

### Added -- Product enrichment, notification, Apple Canada

- Claude API enrichment: `enrich_company_descriptions()` calls `claude-haiku` to fill `Product/Purpose` for each new job; one call per unique company, cached within the run; degrades gracefully if `ANTHROPIC_API_KEY` unset
- Notification opens `jobs.csv` on click via `terminal-notifier` (falls back to plain osascript if not installed)
- Apple scraper now targets `en-ca` (Canada) listings; switched to `domcontentloaded` wait (same SPA fix as Wellfound); enabled in `config.yaml`
- `gather-jobs.sh` deletes previous days' `job-queue-*.md` files on each run so only today's remains

### Added -- Scraper Rewrite

- All 8 job sources now use Playwright with `headless=False` (bot-detection avoidance): RemoteOK, WeWorkRemotely, Anthropic, LinkedIn, Indeed, Wellfound, Apple
- `dedup_key(company, role)` — normalized cross-site dedup key prevents same job from appearing twice across boards
- `load_existing_jobs()` — returns 4-tuple `(known_urls, known_keys, header, next_num)`; loads both URL set and company+role key set from `jobs.csv`
- `_playwright_browser(config)` — shared context manager; non-headless Chromium with realistic Chrome 124 user-agent
- `_compress_search_terms(roles)` — strips seniority prefixes to reduce 21 configured roles to ~10 base search terms, cutting Playwright page loads per site
- `run_all_scrapers()` now returns `(jobs, failed)` tuple; deduplicates within-run by URL AND company+role key
- LinkedIn, Indeed, Wellfound enabled in `config.yaml`; Apple implemented but disabled pending manual testing
- Test suite expanded to 118 tests covering `dedup_key`, `_compress_search_terms`, and all Playwright scrapers via mock context managers

### Added -- Test Suite

- Test infrastructure: pytest + tox config in `pyproject.toml`, coverage minimum 79%
- 94 tests across 8 test files covering scrape-jobs pipeline (96% coverage)
- Test files: config loading, role matching, CSV read/write, HN/RemoteOK/WWR/Anthropic scrapers, orchestrator
- Makefile `test` and `test-cov` targets
- `.gitignore` entries for coverage, tox, and Python build artifacts

### Changed -- Install

- `Makefile` install: stop symlinking `CLAUDE.md` into `.claude/` -- Claude Code auto-loads `<project>/CLAUDE.md` and the symlink caused the same content to load twice in session context
- `Makefile` uninstall: also remove stale `settings.local.json` symlink (was leaked by prior installs)

### Fixed -- Script Hardening

- `open-session.sh`: capture new tab reference from AppleScript `create tab` (was writing command to the previously focused tab instead of the new one)
- `scrape-jobs.py`: fix invalid `dict[str, callable]` type hint (builtin, not a type); use `Callable` from `collections.abc`
- `gather-carryforward.sh`: soft-fail on TSV parsing errors for `carry_forward` and `personal_tasks` (was killing entire day-start workflow)
- `tracker.sh` audit: fix awk field indexes after `GD Rating` column added (was miscounting jobs)
- `tracker.sh` audit: fix jq `contains()` direction for substring company matching via `.as $obj`
- `tracker.sh` next_id: force base-10 arithmetic with `10#$max` (was breaking on `app-010` after `app-009`)
- `tracker.sh` next_id: filter malformed IDs before computing max
- `tracker.sh` update: whitelist updatable fields to prevent arbitrary yq key injection
- `tracker.sh`: use `strenv()` everywhere for yq variable interpolation (injection safety)
- `open-session.sh`: reuse iTerm window via `/tmp/iterm-daily-driver-wid` so repeat launches open tabs, not new windows
- `open-session.sh`: close TOCTOU race with `pending:<epoch>` sentinel and 60s expiry
- `open-session.sh`: never `activate` iTerm (keeps new tabs in the background)
- `gather-sessions.sh`: write jq input to temp file to catch parse failures (was masked by `${PIPESTATUS[0]}` in command substitution)
- `gather-git-activity.sh`: treat `.git` files as repos so worktrees are scanned
- `gather-git-activity.sh`: guard against missing `GIT_DIR` before `git log`
- `standup-dates.sh`: Thursday lookback now `-2d` to include Tuesday activity
- `gather-calendar.sh`: stop suppressing icalBuddy stderr
- `gather-carryforward.sh`: validate carry-forward JSON is an array before iterating
- `gather-notes-range.sh`: validate date inputs against `YYYY-MM-DD` regex before `date -j -f`
- `calendar-sync.sh`: enforce `HH:MM-HH:MM` time-block regex and detect midnight-crossing blocks
- `calendar-sync.sh`: capture osascript errors instead of silently dropping events
- `calendar-check.sh`: match calendar names with `grep -qxF` against trimmed list (avoids partial-match false positives)
- `checkin-state.sh`: clean up temp file on atomic-write failure; validate numeric minute fields
- `focus-mode.sh`: treat malformed `end_epoch` as expired; validate numeric inputs
- `monthly-save-path.sh`: validate `YEAR`, `MONTH`, `MONTH_NAME` shapes before use
- `sync-repos.sh`: warn on non-git directories instead of silent skip; capture `rebase --abort` failures
- `launchd-install.sh`: capture `launchctl bootstrap` errors with actionable messaging
- `scrape-jobs.py` remoteok: handle non-JSON responses (rate-limit returns HTML) instead of crashing
- `scrape-jobs.py` anthropic: wrap Playwright `PWError`/`PWTimeout` around browser launch and navigation
- `scrape-jobs.py`: exit non-zero when any source fails, even in dry-run
- Uniform defensive stderr capture across `calendar-check.sh`, `check-calendar-sync.sh`, `check-output-dir.sh`, `commit-daily-notes.sh`, `commit-monthly.sh`, `commit-weekly.sh`, `ensure-daily-dir.sh`, `gather-applications.sh`, `get-output-dir.sh`, `init-output-dir.sh`, `list-weekly-summaries.sh`, `read-plan.sh`, `read-plan-frontmatter.sh`, `standup-save-path.sh`, `weekly-save-path.sh` (replace `2>/dev/null || true` with explicit errors)

### Changed -- Script Hardening

- `CLAUDE.md`: document `/month-end` and `/interview-prep` commands; add `GD Rating` column to jobs.csv schema

### Fixed -- System Gaps Audit

- `check-in.md`: correct `gather-git.sh` to `gather-git-activity.sh` (broken data gathering)
- `gather-carryforward.sh`: walk back up to 5 business days to find carry-forward items (was single day)
- `gather-carryforward.sh`: pre-increment `carried_days` so day-start copies values as-is
- `gather-git-activity.sh`: scan repos up to 3 levels deep via `find` (was top-level glob only)
- `tracker.sh` follow-ups: include `interviewing` status alongside `applied` and `screening`

### Added -- System Gaps Audit

- `day-end.md`: read check-in state.json as first step; use as ground truth for plan vs actual
- `work-planner.md`: document check-in state as primary source of truth in day-end process
- `checkin-state.sh read`: new subcommand to pretty-print today's state
- `tracker.sh audit`: cross-reference tracker.yaml and jobs.csv for drift detection
- `config.yaml`: `follow_up_by_status` intervals (applied: 7, screening: 3, interviewing: 5 days)
- `config.yaml`: `calendar.excluded_calendars` list for icalBuddy `-ec` filtering
- `gather-calendar.sh`: read excluded_calendars from config, pass to icalBuddy
- `gather-sessions.sh`: detect unclosed sessions via heartbeat (session_start with no matching session_end)
- `scrape-jobs.py`: populate Product/Purpose column with placeholder for auto-scraped rows

### Fixed -- Code Review Findings

- `open-session.sh`: close TOCTOU race with preliminary lock file before iTerm shell startup
- `open-session.sh`: propagate `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` to launchd-launched sessions
- `checkin-state.sh`: fix first-run crash by calling `cmd_init` (creates state file) instead of `ensure_state_dir`
- `focus-mode.sh`: extract `_cleanup_focus()` so `cmd_status` expiry does not open iTerm
- `focus-mode.sh`: stop suppressing stderr from `checkin-state.sh` calls
- `gather-notes-range.sh`: suppress "(no file)" messages on weekends while still showing any weekend files
- `tracker.sh` audit: proper RFC 4180 CSV parsing via awk (was fragile `IFS=','`)
- `tracker.sh` audit: substring company matching to avoid false negatives (`Anthropic` vs `Anthropic Inc`)
- `tracker.sh` audit: pre-build lookup structures to eliminate O(n^2) nested CSV loops
- `tracker.sh`: use `strenv()` in yq for status interpolation (was unquoted shell variable)
- `gather-carryforward.sh`: fix `carried_days` default from `// 1` to `// 0` (off-by-one on first carry)
- `gather-carryforward.sh`: fix misleading "skipped N days" message (now "looked back N business days")
- `gather-carryforward.sh`: remove dead `$NOTES_FILE` variable
- `tracker.sh` audit: remove `exit 1` on discrepancies (diagnostic output, not a gate)
- `tracker.sh` audit: filter tracker-side checks to active statuses only (was flagging rejected/withdrawn)

### Changed -- Code Review Findings

- Batch yq subprocess calls in `tracker.sh`, `gather-carryforward.sh`, and `calendar-sync.sh` from O(N) to O(1) via `yq -o=json | jq @tsv` pipes
- `focus-mode.sh`: remove duplicate `open_iterm()`, delegate to `open-session.sh check-in`
- `Makefile`: add `.SHELLFLAGS := -o pipefail -c` for pipeline failure detection
- `commands/voice-update.md`, `agents/work-planner.md`: replace hardcoded paths with relative `config.yaml`

### Removed -- Code Review Findings

- `config.yaml`: dead keys `stale_days` and `active_statuses` (unused by any script)
- `Makefile`: `terminal-notifier` from `BREW_DEPS` and status check (unused)

### Added -- Job Search Refactor

- Jobs table (`jobs.md`), contacts log (`contacts.md`), location preferences (`location-preferences.md`) documentation in CLAUDE.md
- `/interview-prep` command for structured interview practice (behavioral, technical, system design)
- `make interview-prep` target with company/role argument support
- Personal task carry-forward extraction in `gather-carryforward.sh`
- Playwright MCP server allowed in settings.json
- Writing voice profile (`voice-profile.md`) in output dir: documents language patterns, structural conventions, avoidances, and annotated approved samples
- `/voice-update` command: analyzes approved writing samples or explicit feedback, proposes profile updates, applies after confirmation
- `make voice-update` target with optional `ARGS` for file path input
- `work-planner` agent now reads voice profile before drafting cover letters or professional communications
- Voice profile seeded from user-authored sources: Anthropic cover letter, self-reviews, LinkedIn updates (2026-04-07)

### Fixed -- Job Search Refactor

- `open-session.sh`: use `create window` instead of `create tab` (fixes blank iTerm on fresh boot)
- `open-session.sh`: add `activate` to iTerm and Terminal branches (window surfaces on launch)
- `config.yaml`: remove 09:00 and 17:00 from `checkin.times` (managed by separate day-start/day-end plists)

### Added -- Planning Enhancements

- `/month-end` command for monthly rollup reports with executive summary and metrics
- `/review-prs` command to check pending PR reviews and open iTerm2 review sessions
- `make month-end` and `make review-prs` targets
- Current time capture (step 0) in day-start, check-in, and prep -- plans start from now, not 9am
- Standup time config (`reporting.standup.time`) treated as a fixed daily commitment
- Day-of-week lookback logic for standup (Mon covers Fri, Wed covers Mon-Tue, etc.)
- Standup output saved to `YYYY-MM-DD-standup.md` when `reporting.standup.save` is true
- PR review selection in day-start with iTerm2 sessions via `cldepr` launch mode

### Fixed -- Reliability

- `calendar-sync.sh`: restore defensive `exit 0` with warning on stale-event deletion failure (reverts silent `|| true`)
- `checkin-state.sh`: atomic writes via temp-file-then-rename at all 3 state mutation points
- `check-in-notify.sh` Gate 3: treat missing `jq` as focus lock active (prevents interrupting focus when jq absent)
- `check-in-notify.sh`: remove meeting, recency, and plan-exists gates that silently suppressed all check-ins
- `check-in-notify.sh`: open iTerm2 in background instead of stealing focus

### Removed -- Dead Code

- `scripts/launch-day-end.sh`: no callers, no plist, hard-coded path; deleted
- `checkin-state.sh` `get-overruns` command: written but never consumed by any script
- `agents/work-planner.md` `carry_forward_id` field: written by LLM but never read by any script

### Changed -- Claude Invocations

- All Makefile workflow targets use explicit `--model`/`--effort` flags matching launch mode presets
- `CLDE` launch mode changed from opus/high to sonnet/medium (cost optimization for daily workflow)
- `check-in-notify.sh` and `focus-mode.sh` iTerm2 launch commands updated to sonnet/medium (were opus/high)
- Interactive commands: sonnet, medium effort, sonnet subagents. Headless (standup, focus): sonnet, low effort
- All sessions use `--agent work-planner` and `-n` session naming for `/resume`
- Ticket references now always include summary (e.g., `SRE-1168 - Migrate runner groups`)

### Added -- Check-in System

- `/check-in` command for mid-day plan review, progress capture, and overrun detection
- `/focus` command to toggle focus mode (suppresses check-in notifications)
- `scripts/check-in-notify.sh` launchd notification wrapper with 3 gates (weekend, work hours, focus lock)
- `scripts/checkin-state.sh` for runtime state management (check-in history, overruns, focus sessions)
- `scripts/focus-mode.sh` for focus lock file management with osascript notifications
- `launchd/com.daily-driver.checkin.plist` LaunchAgent template for automated 30-minute check-in reminders
- `scripts/launchd-install.sh` for LaunchAgent install/uninstall
- `make launchd-install` and `make launchd-uninstall` targets

### Added -- Calendar Sync

- `scripts/calendar-sync.sh` writes plan time blocks to a local macOS Calendar via AppleScript
- Configurable calendar name via `calendar.plan_calendar_name` in config.yaml
- Idempotent: re-running replaces events tagged with today's date

### Added -- Jira Enhancements

- RFC project tracking via `scripts/gather-rfcs.sh` (assigned, pending approval, recently approved)
- `scripts/extract-jira-refs.sh` extracts Jira ticket keys from text (git commits, session data)
- `scripts/gather-ticket-status.sh` bulk ticket status lookup for EOD sweep
- `rfc_projects` and `ticket_pattern` per Jira instance in config.yaml
- EOD ticket sweep in `/day-end` comparing worked-on tickets vs Jira status

### Added -- Planning & Carry-forward

- YAML frontmatter in plan and notes files for machine-readable structured data
- `scripts/gather-carryforward.sh` extracts structured carry-forward from yesterday's notes
- Stable `cf-NNN` IDs for carry-forward items across days
- Personal task input during `/day-start` with time/duration tracking
- Status enum: planned, done, blocked, carry-over, unplanned, dropped
- Stale item detection (configurable threshold, default 3 days)
- Blocked item tracking with reasons

### Added -- Reporting

- `/standup` command generates async standup (Yesterday/Today/Blockers) to clipboard
- `/week-end` command aggregates weekly notes into manager-friendly summary
- `/prep` command pulls Jira/PR context for upcoming calendar meetings
- `scripts/gather-notes-range.sh` reads notes/plan files for a date range
- Configurable standup format (slack/plain) and weekly save directory

### Added -- Config & Infrastructure (from v1.1)

- `config.yaml` for centralized configuration
- `README.md` with setup instructions and project documentation
- This changelog
- Second Jira instance support (corescientific.atlassian.net) via `acli auth switch`
- `yq` dependency for YAML config parsing
- `--author` filter in `gather-git-activity.sh`
- 1Password SSH agent check in `/setup`

### Added -- Makefile & CLI

- `make status` target to check installation status of all dependencies and services
- `make launchd-start` target to load LaunchAgent if installed but not running
- `make deps` target for idempotent installation of all dependencies (Homebrew, acli tap, claude)
- `make setup` now runs deps, install, and verifies auth (Jira, GitHub, 1Password, Calendar)
- Workflow targets: `make day-start`, `make day-end`, `make check-in`, `make standup`, `make week-end`, `make prep`, `make focus`
- `make standup` runs headless (`-p --model sonnet`) and pipes output to clipboard via `pbcopy`
- `make focus` runs headless (no interactive session needed)
- `make help` shows quick start guide, typical day timeline, and notes
- All interactive workflow targets use `--agent work-planner` and `-n` session naming for `/resume`

### Changed -- Notifications to iTerm2

- Check-in triggers now open an iTerm2 window with `claude /check-in` instead of sending macOS notifications
- Focus mode disable opens iTerm2 with `/check-in` instead of sending a notification
- `notify()` removed from check-in-notify.sh (no remaining callers)
- Falls back to Terminal.app if iTerm2 is not installed

### Fixed

- BSD sed incompatibility: replaced `sed -n` frontmatter extraction with `awk` in 4 scripts
- Bash 3.2 compatibility: rewrote `gather-ticket-status.sh` without `declare -A` (Bash 4+ feature)
- AppleScript string injection: `esc()` in `calendar-sync.sh` now strips newlines before escaping
- Silent auth failure: `gather-jira.sh` auth-switch now checks exit code and skips with logged message
- Dead config fields `checkin.interval_minutes` and `checkin.jira_status_reminder` removed from config.yaml
- Dead variables cleaned up in `focus-mode.sh` and `checkin-state.sh`
- Redundant `2>&1` removed from `gather-jira.sh` and `gather-prs.sh`
- `gather-sessions.sh` indentation aligned with control flow
- Weekend skip logic in `/day-start` checked yesterday's DOW instead of today's
- `gather-sessions.sh` timestamp guard: date parsing failure no longer matches all entries
- `launch-day-end.sh` now passes `/day-end` to claude
- `Makefile` uninstall target uses dynamic glob

### Changed

- All scripts read configuration from `config.yaml` instead of hardcoded values
- `gather-jira.sh` queries all configured instances with project-scoped JQL
- `gather-prs.sh` uses `while read` pattern (consistent with other scripts)
- `work-planner.md` extended with carry-forward, personal task, RFC, and status handling rules
- `/day-start` rewritten: 8 steps including carry-forward, personal tasks, RFC gathering, calendar sync
- `/day-end` rewritten: 10 steps including ticket sweep, RFC status, frontmatter save
- `/setup` extended with calendar sync check, launchd status, RFC test

## [0.1.0] - 2026-03-30

### Added

- Initial release
- `/day-start` morning planning command
- `/day-end` end-of-day review command
- `/setup` one-time setup verification command
- `work-planner` agent for planning behavior
- Data gathering scripts: calendar, Jira, PRs, Claude sessions, git activity
- Repo sync script
- Makefile-based install/uninstall for Claude Code integration
- Pre-commit hook for automatic symlink sync
