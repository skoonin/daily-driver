# Changelog

Daily Driver is a pre-1.0 personal tool with no external users. This file
is a rolling summary of the current state; granular history lives in `git
log`. Versioned release history starts at 1.0.

## [Unreleased]

### Fixed

- **generate fallbacks no longer silently corrupt installs or discard user `settings.local.json`**: three bare `except Exception` catches in the generate path were masking real failures. `_render_settings` and `_render_initial_config` now narrow to the expected error types and log the offending path/template at WARNING before falling back. A malformed existing `settings.local.json` is now copied to `settings.local.json.invalid` (and logged) before the rendered defaults replace it, instead of being discarded silently on the next `doctor --fix`.
- **Three concurrency races (P0 correctness)**: (1) `tracker update --note` lost
  one of two concurrent appends — the note was read and concatenated outside the
  lock, then clobbered the freshly-loaded entry inside it; the append now happens
  inside `Tracker.update` under the lock via a new `append_note` argument. (2)
  `voice-update` locked the data file itself, so `apply_update`'s `os.replace`
  severed the locked fd from the live inode and broke mutual exclusion between
  concurrent runs; it now locks a sentinel `voice-profile.md.lock`. (3) `jobs
  prune` read and classified rows before acquiring `.jobs.lock`, so a concurrent
  `jobs run` append between the read and the rewrite was silently deleted; the
  read and classification now run inside the lock.

### Removed

- **Dead code in scraper module**: deleted `scraper/runner.py` `__all__` block (mis-described public surface), removed `scraper/runner.py:_to_int` duplicate (the surviving copy lives in `scraper/comp.py`), and dropped the parallel `SOURCE_REGISTRY` + `_typed_source` wrapper from `scraper/sources/__init__.py` (its lone consumer in `cli/commands/help.py` now enumerates `SCRAPERS` directly). Tautological `tests/test_scraper/test_source_registry.py` removed.

### Added

- **`.dd-config.yaml.j2` is codegen'd from `core/config_models.py`**: every Pydantic `Field` now carries `description=` + `json_schema_extra` template metadata; `core/config_template.py` walks `Config` and emits the template. `make config-template` regenerates, `make check-config-template` (wired into `lint`) fails on drift, and a pre-commit hook catches stale templates locally.
- **install-smoke CI**: workspace scaffold step (`init + doctor + tracker add/list`) catches missing package-data regressions; smoke now also runs on direct pushes to `main`.
- **Scraper tests**: dedicated test modules for `weworkremotely` (RSS) and `apple` (Playwright/JSON API) with committed fixtures under `tests/fixtures/scraper/`.
- **`integrations/notify.py`**: `desktop_notify()` wrapper lifts osascript/terminal-notifier subprocess calls out of `scraper/runner.py` into the integrations layer; tests in `tests/test_integrations/test_notify.py`.
- **generate double-check locking test**: `test_concurrent_invocations_only_one_wipe` asserts the flock + re-check-inside-lock pattern prevents duplicate wipes under concurrent invocation.
- **release CI gate**: `tox -e py311,py312` (both interpreters set up explicitly) runs before `python -m build`; post-build wheel install smoke confirms package-data before artifacts attach to the GitHub Release.
- **SHA-pinned GitHub Actions**: all four third-party Actions (`checkout`, `setup-python`, `upload-artifact`, `softprops/action-gh-release`) pinned to 40-char commit SHAs with trailing version comments. Bumped to current latest (major-version jumps across the board). Dependabot `github-actions` ecosystem keeps SHAs current.
- **`make test-quick-parallel`**: exposes the existing `tox -e test-parallel` env (pytest-xdist `-n auto`) for fast inner-loop runs.

### Removed

- **Dead config models deleted** (schema break): `Compensation`, `RoleFilters`, `VoiceProfile`, and `PlaywrightDelays` had zero production readers — config theater with no runtime effect. Their fields (`plugins.job_search.compensation`, `plugins.job_search.role_filters`, `voice_profile`, `scraper.playwright_delays`) are gone, and the matching commented template stanzas were scrubbed. Workspaces carrying any of those blocks now fail validation. Voice persona lives in `voice-profile.md`, not in config.

### Changed

- **Logging now uses `core.logging.get_logger` consistently** across the CLI and scraper layers, removing bare `logging.getLogger` and a stray `print(..., file=sys.stderr)` in the tracker. All loggers live under the `daily_driver` namespace so verbosity flags and the Rich handler apply uniformly.
- **CLI startup latency reduced via lazy command/import loading.** The parser no longer imports every subcommand module up front; argv is scanned for the invoked command and only that module's parser is built (other commands get help-only stubs for the top-level listing). `init` defers `core.generate` (Jinja2 + pydantic) and `summary` defers `core.summary` (requests ~47ms), `ai_provider`, and `clipboard` into their consumers. Warm `daily-driver --version` drops from ~240ms to ~180ms wall-clock (~200ms to ~145ms in-process); residual cost is `core.config`/pydantic pulled transitively via workspace resolution.
- **BREAKING: `.dd-config.yaml` root is now strict (`extra="forbid"`).** Unknown top-level keys raise `pydantic.ValidationError` at parse time instead of being silently accepted, so typos like `tracer:` for `tracker:` fail loudly. Pre-existing top-level user keys must move to a documented seam: per-entry data goes under `tracker.extras` (via `daily-driver tracker add --extra key=value`), per-category fields under `tracker.categories.<name>`, and narrative context into `voice-profile.md` (or a sibling `.notes.md` in the workspace).
- **Console stream tests**: rewrote four placeholder tests in `test_console.py` to actually capture stdout/stderr with `capsys` and assert routing; added `test_user_output_is_stdout_not_stderr` regression guard.
- **`scheduler` config is now a typed `SchedulerConfig`** (`checkin.times` list + `jobs.time` string) instead of a freeform `dict[str, Any]`. Unknown scheduler keys — including the legacy `scrape_jobs` — are now rejected at parse time via `extra="forbid"`, replacing the previous runtime "scrape_jobs was renamed" error message.
- **Subprocess calls funnel through `integrations/`.** Git and icalBuddy invocations moved out of `gathers/` into new `integrations/git.py` and `integrations/icalbuddy.py`; `claude_cli` now raises domain exceptions (`ClaudeInvocationError`, `ClaudeTimeoutError`) instead of leaking `subprocess.CalledProcessError` / `TimeoutExpired` to the CLI. Scraper HTTP funnels through `sources/_http.py` (it re-exports `Session` / `HTTPError` / `HTTPTimeout`); `scraper/runner.py` and `scraper/enrichment.py` no longer import `requests` directly.
- **Workspace + output_dir resolution consolidated into `cli/_common.resolve_workspace`.** The discover-or-fail boilerplate copy-pasted across the CLI commands now flows through one helper that raises `WorkspaceError`; the two divergent helpers (`scheduler._resolve_workspace` returning `None`, `_claude_session.resolve_workspace` raising `SessionError`) are removed, and both `_resolve_output_dir` re-implementations (jobs, doctor) now defer to `Workspace.output_dir`.
- **`summary` AI-routing now reads the validated `Config`** instead of a separate `yaml.safe_load` of `.dd-config.yaml`, so config validation is consistent across commands.
- **Top-level CLI errors now show the exception class + cause** without needing `-v`: the non-verbose error path prints `ClassName: message` (and `caused by: ...` one level deep) instead of a bare `str(exc)`, and always logs a full traceback (best-effort) to `<state_dir>/logs/cli-error.log`.

### AI providers

- **Ollama backend for headless tasks** via the `ai:` config block.
  `enrichment` and `summary` route per-task (`provider: claude | ollama`);
  each has its own `model` field. Omitting the block preserves claude-only
  behavior.
- **Parallel ollama enrichment** via `ai.ollama.max_parallel` (default 4).
  Per-worker log tags (`[enrich w2]`) help trace interleaved failures.
- **`AI providers` doctor row** reports reachability, confirms the
  configured model is pulled, and shows effective parallelism. Omitted
  when no task routes to ollama.
- **Interactive launchers stay claude-only** (session resume, agents,
  workspace `--add-dir` context that ollama does not provide).

### Jobs scraper

- **JobSpy split into per-site scrapers.** `jobspy` now expands to three
  registry entries — `jobspy_linkedin`, `jobspy_indeed`, `jobspy_google` —
  so all three sites run concurrently in the Phase 1 parallel pool instead
  of serially inside one upstream call. Config stays under a single
  `jobspy:` key, now a nested block with per-site flags (`enabled`,
  `linkedin`, `indeed`, `google`); the legacy `jobspy: true|false` bool
  still coerces.
- **Fix: Apple scraper now runs in non-headless mode.** `_non_headless_sources`
  previously relied on a `type: playwright` config key that `SourceToggle`
  (`extra="forbid"`) silently rejects, so Apple always fell into the headless
  phase and crashed on macOS with a Mach port error. Classification now uses
  the code-level `_PLAYWRIGHT_SOURCES` constant.
- **Playwright browser switched from Chromium to Firefox.** `playwright install
  firefox` is now required for the Apple scraper. Default `user_agent` updated
  to a Firefox/128 UA to match. Validated parity: 120 jobs / 115s (Firefox) vs
  121 jobs / 114.5s (Chromium).

- **Shared `.jobs.lock`** serializes `jobs run` / `jobs prune` / `jobs run
  --backfill` mutations on `jobs.csv`.
- **Ctrl-C during `--backfill` flushes partial progress** and names the
  pre-run `jobs.csv.bak.<unix>` snapshot in the interrupt message. Second
  Ctrl-C force-quits. CLI exits 130.
- **HN sources hardened**: 429 retry/backoff, Algolia thread discovery,
  new `hn_jobs` source, age filter. Detail-fetch skipped for HN and
  Indeed (no useful enrichment).
- **Backfill counters now exclude `status=skipped` rows.** `Backfill
  complete` reports `+N Notes` (previously hidden). `enrich-fit-notes`
  logs startup + end-of-pass INFO lines so silent ollama failures are
  visible.
- **`Jobs backups` doctor row** warns when more than 5 `jobs.csv.bak.*`
  files accumulate.
- **Backups moved to `<output_dir>/backups/`** with human-readable UTC
  ISO-8601 stamps (`jobs.csv.bak.YYYY-MM-DDTHH-MM-SS-ffffffZ`, hyphens
  for Windows filesystem portability). The `Jobs backups` doctor row
  globs the new location; existing `jobs.csv.bak.<unix>` snapshots in
  `output_dir/` are left in place for manual cleanup.
- **`jobs run` per-source progress on stdout**: each scraper prints
  `Now checking <id>...` before starting and a `<id>: N jobs (Xs)` or
  `<id>: failed (reason)` summary on completion, so the user sees
  activity before any scraper finishes. Lines are atomic; ordering
  across the parallel phase is non-deterministic.
- **JobStatus rename**: `archived` → `dropped`. `_migrate_legacy_header`
  rewrites legacy `Status=archived` rows on next scraper / backfill
  invocation. Hard break per the MVP no-compat policy.
- **Category-aware tracker statuses**: `category=job` entries warn
  against statuses outside `{found, skipped, applied, rejected,
  dropped}`; other categories keep the generic recommended set.

### Logging and output

- **Two-stream Console** (`core.console.Console`): data payloads on
  stdout, status / warnings / errors / logs on stderr. Module loggers
  share `Console.get_log_console()` for theme consistency.
- **Repeatable verbosity**: `-v` = INFO, `-vv` = DEBUG, `-q` = errors
  only. Single `-v` no longer jumps straight to DEBUG.
- **`--no-color` decoupled from markup.** Color is suppressed via
  `color_system=None`; Rich markup and highlights stay on so tables and
  styled prefixes still render cleanly.

### CLI

- **W8 polish**: `scheduler {install,uninstall,status}` group replaces
  the old top-level commands; `materialize` renamed to `generate`;
  focus/tracker/status improvements; short flags across subcommands;
  idempotent `init` and `doctor --fix` (restores deleted managed files).
- **`/interview-prep` → `/daily-learning`** slash command rename.
- **W2 breaking rename**: `scrape-jobs` family → `jobs run` / `jobs
  status` / `jobs prune`.

### Docs

- **Restructured into Diataxis quadrants.** New `docs/README.md`
  navigation index, new `docs/concepts.md` (mental model + workspace
  layout), `customization.md` merged into
  `configuration.md#customization`, developer docs moved to `docs/dev/`.
- **`docs/cli-tree.md`** reframed as orientation-only reference.
- **New `docs/ollama-setup.md`** walkthrough for the Ollama provider.
