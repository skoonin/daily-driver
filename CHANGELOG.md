# Changelog

Daily Driver is a pre-1.0 personal tool with no external users. This file
is a rolling summary of the current state; granular history lives in `git
log`. Versioned release history starts at 1.0.

## [Unreleased]

### Added

- **Selectable Playwright browser engine**: `plugins.job_search.scraper.browser` (`firefox` default, or `chromium`/`webkit`) chooses the engine for browser-driven sources (Apple). The launcher resolves it via `getattr(pw, engine).launch(...)`, the `Literal` field rejects unknown engines at config-load, and `doctor` / `doctor --fix` check and install whichever engine is configured rather than always Firefox. (#68)

### Changed

- **JobSpy source ids shortened**: `jobs run -S` (and the source registry) now
  use `linkedin` / `indeed` / `google` instead of `jobspy_linkedin` /
  `jobspy_indeed` / `jobspy_google`. The `sources.jobspy.*` config block is
  unchanged.
- **HN "Who's Hiring" now surfaces up to 500 matching posts** (was 100): the
  default `hn_max_posts` cap was hitting on every run, so the same first 100
  relevance-ranked roles recurred and nothing past them was ever scraped.
  Raised to 500 (the thread fetch already pulls the whole thread).
- **`jobs run` now shows live progress instead of going silent**: in normal
  mode the run renders a phased live block — a `Scraping sources` group listing
  every source (pending -> running -> done, with a static status marker), then
  an `Enriching jobs` group with detail / company-product / fit-and-notes
  counters — so a long run is visibly alive rather than looking hung. A
  per-source breakdown (`found / new / already in csv / skipped by location`)
  and a reconciling `Completed:` line print at the end. The live block renders
  on any interactive terminal and stays pinned; verbosity controls only how much
  scrolls above it (normal: warnings only; `-v`: INFO heartbeats and per-source
  timings; `-vv`: the full stream). Problems surface live above the block as
  they happen, with a terse end-of-run `Warnings: N (shown above)` line, instead of being
  held back to a section at the end. During the serial Apple phase the browser
  runs headless while the block is pinned so its window can't cut into the
  display. Non-interactive output (cron, launchd, pipes) falls back to plain
  lines with no ANSI. All human progress now goes to stderr, leaving stdout for
  the dry-run table and a future `--json`. (#71, #72)
- **Generated `.dd-config.yaml` now surfaces every user-configurable setting**:
  the scaffold exposes the scraper transport knobs (`user_agent`, `timeout`,
  `search_terms`, `parallel_workers`, `max_pages`), the per-source knobs nested
  under each source (`wwr_categories`, `greenhouse_boards`, `hn_max_posts`, and
  the `jobspy.jobs` query block), and `ai.ollama.max_parallel` — all previously
  valid config but hidden from the template. `scraper.headless` stays out: it is
  overridden per scrape phase, so a value set there has no effect. (#71)
- **BREAKING: removed the `plugins.job_search.locations.cities` config field**
  and the city branch of `location_matches`; filtering is now on `countries`
  (and `remote`) only. Hard break, no compat shim (pre-1.0 personal tool). (#70)
- **Apple scraper now scopes by Apple's postLocation code** (resolved live via
  the `jobs.apple.com` refData API), navigating `en-us/search?location=x-<CODE>`
  so titles return in English for every country; country lookups moved from
  `sources/_http.py` to a new `scraper/countries.py` module. Removes the old
  six-country ceiling — Apple now covers any country it posts in. (#70)

### Fixed

- **Apple jobs no longer dropped by the location filter**: the Apple scraper
  emitted a bare city (`"Seattle"`) as the job location, which matches no
  country-name alias, so `location_matches` silently dropped every Apple job
  when filtering by country (the run funnel collapsed to `new → 0 after
  location`). Apple's API hands `countryName` in the same record; the scraper
  now joins city, state/province, and country into `"Seattle, United States of
  America"`, so the existing country branch matches. Fix is Apple-only — other
  bare-location sources (greenhouse, HN, remoteok, wwr) genuinely lack a country
  in their upstream data and are left as a follow-up. (#69)

## [0.1.0] — 2026-06-01

### Fixed

- **Compensation now recovered for non-US jobs**: JobSpy only regexes salary
  out of job descriptions for US listings, so Indeed/LinkedIn jobs in Canada
  (and every other country) landed with a blank `Comp`. `scraper/sources/jobspy.py`
  now runs the same description extraction for every search country and
  annualizes hourly/monthly figures so the `Comp` column reads as a comparable
  yearly amount.
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

### Changed

- **Fit scoring now reads `context.md`**: the fit/notes enrichment prompt injects the workspace's `context.md` (when present) into every job evaluation, so fit weighs how well the candidate's real experience matches the role — not just the one-line `persona`. Notes now justify the score and the location fit instead of listing the tech stack; the criteria block is reworded to state the ask explicitly. Without a `context.md`, fit falls back to role/company/location scoring as before. `jobs run` logs a token-cost estimate when `context.md` is large, since it rides every per-job call. The scaffolded `context.md` template gained Experience/Skills and Location-preferences sections to match.
- **Location is the only filter that removes jobs**: `jobs run` surfaces every
  job matching your configured countries/cities. Compensation is display-only —
  the scraper writes whatever amount it finds to the `Comp` column and never
  filters, drops, or flags a job on it.
- **`jobs run` output reconciles the filter funnel**: the location-filter count,
  enrichment failure/skip counts, a real-run enrichment heads-up, and a one-line
  `Funnel: N scraped → … → K written` summary are now shown at default
  verbosity, so jobs no longer vanish between pipeline stages unexplained.
- **Country support is derived from JobSpy's `Country` enum** (~70 countries,
  was a hand-maintained 6). Any country JobSpy can scrape now works for both the
  location filter and Indeed search, with no per-country code to add. Bare
  2-letter ISO codes are still excluded from location matching to avoid false
  positives (e.g. "us" matching "Austin").
- **Comp is no longer labeled with a currency**: recovered comp keeps the
  source's own symbol instead of being re-stamped per search country; the number
  alongside the location is enough.

### Removed

- **Compensation is now a plain display string**: the typed `Comp` model (with
  its free-form parser, currency normalization, and period handling), the
  `plugins.job_search.min_comp_usd` config field, the comp-threshold filter pass,
  and the `skipped-comp` status are all gone. The scraper still extracts whatever
  amount it finds and writes it to the `Comp` column verbatim, but comp never
  filters, drops, or flags a job — location is the only filter that removes jobs.
  Existing configs that set `min_comp_usd` must remove it (the config model is
  `extra="forbid"`).
- **Currency filter and FX conversion**: the `plugins.job_search.primary_currency`
  config field, the currency-mismatch drop, and the `_fx` USD-conversion table
  are gone. Comp is shown in the listing's own currency and non-matching
  currencies are no longer dropped (existing configs with `primary_currency` set
  must remove it — the config model is `extra="forbid"`).
- **Dead code in scraper module**: deleted `scraper/runner.py` `__all__` block (mis-described public surface), removed `scraper/runner.py:_to_int` duplicate (the surviving copy lives in `scraper/comp.py`), and dropped the parallel `SOURCE_REGISTRY` + `_typed_source` wrapper from `scraper/sources/__init__.py` (its lone consumer in `cli/commands/help.py` now enumerates `SCRAPERS` directly). Tautological `tests/test_scraper/test_source_registry.py` removed.
- **Legacy CSV scaffolding**: the on-disk `jobs.csv` legacy-header / `archived`-status migration is gone — the current canonical header is now assumed (a non-canonical `jobs.csv` is used as-is rather than rewritten). Also removed the unused dict-based `append_jobs` writer (the typed `append_jobs_typed` is the only writer) and collapsed the two dry-run table renderers into a single typed one.
- **Legacy launchd label sweep and the dead `plan_summary` daily-state field**: plugins no longer carry `legacy_launchd_labels` (the scheduler only manages current labels); the `daily-state.yaml` `plan_summary` field (never written by the app) is dropped — an existing daily-state file still carrying it will be rejected on read (the model is `extra="forbid"`).

### Added

- **Config-driven criteria scanner for enrichment**: `plugins.job_search.enrichment.criteria` lets users declare extra things to assess in each job description (`{label, assess}` pairs). The scanner rides the existing fit/notes enrichment call — no new LLM requests — extending that prompt's JSON contract with a `criteria` object; meaningful answers fold into the `Notes` column as `Label: value` segments. Quiet by default: empty, whitespace-only, `unknown`, and `n/a` answers (case-insensitive) add nothing, so the common case stays clean. With no criteria configured, the fit/notes prompt is byte-for-byte unchanged. The whole `enrichment` block is now surfaced (commented) in the scaffolded `.dd-config.yaml` with per-knob descriptions, so timeout, cost caps, and which passes run are user-editable.
- **`doctor` check for the Playwright Firefox browser**: when a Playwright source (Apple) is enabled, `doctor` warns on macOS if the browser binary is missing (the pip package installs without it, so the source dies at launch), and `doctor --fix` installs it via a new `integrations/playwright.py` wrapper. Plugin doctor checks can now carry a self-contained `plugin_fixer` callable, run by `_run_plugin_fixers` — so `--fix` repairs plugin findings without core importing plugin code (core's own drift/contract fixes stay dispatched by name via `generate()`).
- **Per-provider enrichment concurrency**: enrichment now fans out for claude as well as ollama. A new `ai.claude.max_parallel` knob (default 4, lower than ollama since claude is rate-limited and runs one CLI subprocess per call) controls claude worker threads; `_ollama_pool_size` is replaced by a provider-agnostic `_enrich_pool_size`. Parallelism raises throughput only — it does not change the `max_enrich_*` budgets or the per-call timeout.
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

- **BREAKING: `plugins.job_search.scraper` decomposed into three sibling sub-blocks (schema break).** The 25-field `ScraperConfig` god-object is grouped into focused sub-models by concern — `scraper` (transport / retry: `enabled`, `max_retries`, `max_age_days`, `user_agent`, `timeout`, `search_terms`, `headless`, `parallel_workers`, `max_pages`), `enrichment` (`enrich_timeout`, `max_enrich_companies`, `enrich_gd_rating`, `enrich_fit`, `enrich_notes`, `max_enrich_fit`, `detail_delay_seconds`), and `sources` (the source-toggle dict) — with per-source typed toggles (`extra="forbid"` catches knob typos). Per-source knobs that were flat on `scraper` now live on their `SourceToggle` subclass: `wwr_categories` → `weworkremotely`, `greenhouse_boards` → `greenhouse`, `hn_max_posts` → `hn_who_is_hiring` / `hn_jobs`, and the jobspy query knobs (`results_wanted_per_query`/`hours_old`/`country_indeed`, the former `JobsConfig`) → `jobspy.jobs`. All four models are `extra="forbid"`, so a flat `scraper:` block carrying a moved field now fails validation. No compat shim — existing workspace configs must restructure. Before:

  ```yaml
  plugins:
    job_search:
      scraper:
        enabled: true
        enrich_timeout: 45
        max_enrich_companies: 30
        detail_delay_seconds: 0
        wwr_categories: [devops, sysadmin]
        greenhouse_boards: [anthropic, stripe]
        hn_max_posts: 50
        sources:
          weworkremotely: true
          greenhouse: true
          hn_jobs: true
          jobspy:
            enabled: true
            results_wanted_per_query: 25
  ```

  After:

  ```yaml
  plugins:
    job_search:
      scraper:
        enabled: true
      enrichment:
        enrich_timeout: 45
        max_enrich_companies: 30
        detail_delay_seconds: 0
      sources:
        weworkremotely:
          enabled: true
          wwr_categories: [devops, sysadmin]
        greenhouse:
          enabled: true
          greenhouse_boards: [anthropic, stripe]
        hn_jobs:
          enabled: true
          hn_max_posts: 50
        jobspy:
          enabled: true
          jobs:
            results_wanted_per_query: 25
  ```

- **Job search extracted into a real plugin (`plugins/job_search/`).** The feature's config, CLI, scraper sources, scheduler jobs, and doctor checks now live under `source/daily_driver/plugins/job_search/` and are wired through a static `Plugin` contract (`plugins/_base.py`) listed in `PLUGINS`. Core no longer hardcodes job-search specifics: `core.scheduler`, `core.doctor`, and `core.generate` iterate `PLUGINS` (importing it lazily so core stays import-light), and `PluginsConfig` is assembled from the registry (`extra="forbid"`, matching the strict root). `Plugin.package_data_dirs` adds an extension point so a future plugin can ship slash-commands; `job_search` ships none, but the generate path is exercised by a synthetic-plugin test. Deliberate non-changes for honesty: scraper **source IDs were not namespaced** (qualifying them would break existing workspace configs and CLI args — deferred to a future migration); the **`plugins.job_search` config namespace is unchanged**, so no user migration is needed; and **`integrations/notify.py` stays the generic desktop-notify primitive** (the W11 integrations boundary), not absorbed into the plugin.
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
