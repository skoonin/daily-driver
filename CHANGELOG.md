# Changelog

Daily Driver is a pre-1.0 personal tool with no external users. This file
is a rolling summary of the current state; granular history lives in `git
log`. Versioned release history starts at 1.0.

## [Unreleased]

### Added

- **`jobs run --json`**: after the run, emits the run manifest (`jobs-last-run.json` content) to stdout for scripting. The live progress block is suppressed and all diagnostics stay on stderr, so stdout is clean JSON you can pipe into `jq`. Mutually exclusive with `--dry-run`; an interrupted run still emits the manifest (exit `130` / `143`). (#99)

- **One status vocabulary across the tracker and `jobs.csv`**: status spelling is now normalized everywhere (case-folded, underscores and spaces turned into hyphens), so `ruled_out`, `Ruled Out`, and `ruled-out` are the same status — spelling only, never meaning. The `jobs.csv` `Status` column shares the tracker's recommended-status machinery; the job-search set is `found`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, `closed`. `jobs backfill` and `jobs prune` canonicalize the spelling of any row they already rewrite and now print a one-line notice when they do (e.g. `Canonicalized 3 status spelling(s) (e.g. Ruled_Out -> ruled-out)`); a deliberately blank `Status` stays blank rather than being relabelled `found`. A status outside the recommended set logs one WARNING naming the offending values — meaningful for hand-edited cells and `backfill`/`prune` over older data, since a normal `jobs run` writes `found` for every new row. The new `tracker.extra_statuses` config list extends the tracker's recommended set without warnings. (#98)

- **`jobs backfill` is now a first-class subcommand** for re-enriching empty fields on existing `jobs.csv` rows. It shares the same enrichment driver as `jobs run` — a `Job backfill` live progress block with the Detail pages / Company products / Fit and notes phase rows, the overlapped product + fit/notes coordinator under one shared concurrency cap, periodic saves every ~25 results, and the ollama reachability preflight before any LLM call. `-n` / `--dry-run` reports the per-phase would-enrich counts and makes no LLM calls and no writes; `--limit N` caps LLM spend by bounding both the product and fit/notes budgets at `N`. A pre-mutation backup is written under `backups/`, and a Ctrl-C or `SIGTERM` saves partial progress (exit `130` / `143`). (#97)

- **`jobs run` is crash- and interrupt-resilient**: the run now writes to `jobs.csv` as it works instead of holding everything in memory until one append at the end. Each source's new rows are appended as that source finishes, and enrichment rewrites the file after each phase and periodically within the long LLM phases. A Ctrl-C, a scheduled-run `SIGTERM`, or a crash drains in-flight work, saves it, and exits — losing at most the one source still scraping or the last enrichment batch; run `jobs backfill` to finish the empty fields. `SIGTERM` now joins the graceful path (it was previously unhandled): exit `130` for `SIGINT` (Ctrl-C), `143` for `SIGTERM`. While an Apple scrape runs, the earlier sources' rows enrich concurrently so the run finishes sooner. `jobs-last-run.json` gains `phase_reached` and `interrupted` (written on every exit, so `jobs status` never shows a stale "complete"), and `jobs status` prints a recovery line when the last run was cut short. If a source can't be written it is marked failed and the rest of the run continues; if periodic saves hit a disk error the run warns and retries the save at the end rather than failing silently. `--dry-run` is unchanged (in-memory, no writes). (#93)

- **Ollama preflight at `jobs run` start**: when enrichment routes to ollama and at least one LLM pass is on, the run pings the server once (a short 3s reachability probe) before the per-job enrichment loop. If the server is unreachable or the configured model is not pulled, the product / fit / notes passes are skipped with a single warning naming the endpoint or model — instead of letting each job burn a full per-call timeout against a down server. Detail-page enrichment (plain HTTP) still runs, the scraped rows are still appended, and the run manifest records zero LLM enrichment counters; fill the empty rows later with `jobs backfill`. The claude provider is unaffected. (#92)

- **`Remote` column in `jobs.csv`** (immediately after `Location`): records whether a job is `remote`, `hybrid`, `onsite`, or blank. A free heuristic fills `remote` at scrape time when the raw location or title carries a remote token (so `--no-enrich` runs still get it); the fit/notes LLM pass refines it to a definite `remote`/`hybrid`/`onsite` from the description at no extra cost, gated by the new `plugins.job_search.enrichment.enrich_is_remote` toggle (default `true`). Hand-entered values are preserved verbatim and never overwritten by a blank or unrecognized answer. Older `jobs.csv` files without a `Remote` column read with a blank `Remote`; the column is added on the next rewrite. (#91)
- **Per-request `num_ctx` for ollama enrichment**: each ollama call now sends an `options.num_ctx` sized to the prompt (estimated tokens + output headroom, floored at 4096, capped at 16384). Ollama's effective context default is machine-dependent and silently truncates longer prompts; sizing it per request keeps enrichment prompts from being cut off. (#90)
- **`plugins.job_search.enrichment.enrich_product` toggle** (default `true`): gates the company Product/Purpose lookup independently of the Glassdoor rating. The two share one LLM call per company; with both `enrich_product` and `enrich_gd_rating` off the company pass is skipped entirely, and with only one on the prompt asks for and writes only that field. (#90)
- **Detail-phase skip breakdown**: the `jobs run` detail phase summary now itemizes why pages were skipped, e.g. `0 enriched, 7 skipped (5 already complete, 2 blocked host)`, instead of a bare skip count. (#90)
- **`jobs run --no-enrich`**: scrape, dedup, location-filter, and append rows
  without running any enrichment — skips detail pages, company products, and
  fit/notes (no enrichment bars, no detail-page or LLM calls). For fast, cheap
  runs; fill the empty fields later with `jobs backfill`. (#89)
- **Selectable Playwright browser engine**: `plugins.job_search.scraper.browser` (`firefox` default, or `chromium`/`webkit`) chooses the engine for browser-driven sources (Apple). The launcher resolves it via `getattr(pw, engine).launch(...)`, the `Literal` field rejects unknown engines at config-load, and `doctor` / `doctor --fix` check and install whichever engine is configured rather than always Firefox. (#68)
- **`enlighten` runtime dependency** (reverses the earlier "no enlighten"
  decision): the `jobs run` live display is now built on enlighten's terminal
  scroll region instead of Rich's `Progress`. Rich is unchanged for every other
  table (`status`, `tracker`, `doctor`, dry-run output).

### Removed

- **`jobs run --backfill` is removed** in favor of the dedicated `jobs backfill` subcommand (hard break, no alias). The `--backfill` flag, its routing, and its interrupt special-case on `jobs run` are gone; the recovery hints in `jobs status`, the ollama preflight warnings, and the `--no-enrich` help now point at `jobs backfill`. (#97)

- **Google (Google for Jobs) source dropped.** It's broken upstream in
  `python-jobspy` 1.1.82: Google for Jobs results load via a JS + anti-abuse
  token flow, so JobSpy's plain HTTP request gets a shell with no job data and
  returns 0 (verified with a real Google-Jobs search-box query). It also mostly
  aggregated boards we already scrape directly. The `google` source id, its
  `sources.jobspy.google` toggle, and the `scrape_jobspy_google` adapter are
  removed; `jobs run -S google` and `google: true` in config are no longer valid.

### Changed

- **`jobs run` exit code now reflects partial success.** It exits `0` only when every source succeeded and persistence was clean; it exits `1` when any source failed or when periodic saves degraded during the run — even if the final save recovered the data on disk — so a scripted or scheduled caller can tell a fully clean run from a degraded one. (#99)
- **`jobs run` live progress block shows the company currently being enriched.** The Company products and Fit and notes phase bars now fold the company name into their label as each job is processed, so a long enrichment phase shows live which work it is doing rather than only a count. A per-job enrichment failure now logs a one-line summary at `-v` (INFO), so a single failed job is identifiable without `-vv`. (#99)
- **`init` surfaces a broken config template on the terminal.** When the bundled `.dd-config.yaml` template can't be rendered, `init` now warns on the terminal (not just in the log) that it wrote a minimal fallback config with job-search settings absent; when the packaged template is missing entirely (a broken install), `init` fails loudly instead of silently scaffolding a half-configured workspace. (#99)

- **`jobs.csv` `Location` is now geography-only, country-first for newly scraped rows.** Each value is normalized to a canonical full country name first, then the city/region remainder in original order (e.g. `Amsterdam, North Holland, Netherlands` becomes `Netherlands, Amsterdam, North Holland`). Remote tokens are stripped from `Location` (remote-ness now lives in the `Remote` column), so a remote-only posting has a blank `Location` instead of the literal `Remote` — including parenthesised and connective forms (`Remote (US)` becomes `United States`, `San Francisco or Remote` becomes `San Francisco`). When a string names more than one country (`US or Canada`), the first in text order wins and the connective + later country are dropped. When the scraped text names no country, the per-search country a source knows (LinkedIn/Indeed, Apple) supplies it. Only new rows are normalized — stored `Location` values in an existing `jobs.csv` are read faithfully and left as-is (no auto-migration). The location allow-list filter is unaffected — it still runs on the raw scraped text. (#91)
- **`jobs.csv` column order changed**: `Remote` is inserted immediately after `Location`, and `Product/Purpose` moves to immediately after `Notes`. Reads are header-name-based, so files written in the old order still load; they adopt the new order on the next rewrite. Unknown / hand-added columns (e.g. a user's own `Priority`) are carried through a `jobs backfill` rewrite verbatim, positionally — both the header label (appended after the canonical columns) and every cell, even on rows with a blank or duplicate Link. Stored cell values are not otherwise changed. (#91)
- **BREAKING: AI enrichment routing moved off `ai:` onto the job_search plugin.** The core `ai:` block keeps `summary` routing and the shared `claude:` / `ollama:` provider-connection blocks; the enrichment `provider` / `model` now live under `plugins.job_search.enrichment` (where enrichment is actually used). `ai.enrichment` is rejected — there is no compat shim. Move the routing (#90):

  ```yaml
  # Before (rejected):
  ai:
    enrichment:
      provider: ollama
      model: qwen2.5:14b

  # After:
  plugins:
    job_search:
      enrichment:
        provider: ollama
        model: qwen2.5:14b
  ```

- **AI timeout warnings now name the provider**: an enrichment timeout logs `<provider> timed out after Ns` (e.g. `ollama timed out after 60s`) instead of a generic message. For ollama, the first timeout in a run also logs a one-time hint that queued requests count against the timeout and to set `OLLAMA_NUM_PARALLEL` (or `ai.ollama.max_parallel: 1`). (#90)
- **`jobs run` now fills the Product column for newly found jobs during the
  run** (previously only `jobs backfill` filled it). Capped by
  `max_enrich_companies` (default 50) per run. (#86)
- **Faster CLI startup**: template-rendering dependencies now load only during
  `init`, not on every command invocation. (#76)
- **LinkedIn + Indeed merge their backend fetch when their knobs match**:
  when both `linkedin` and `indeed` are enabled with equal
  `results_wanted_per_query` and `hours_old`, they are requested in a single
  underlying call per search (faster, fewer requests) rather than two; differing
  knobs fall back to one call per site. This is internal — the selectors,
  config keys, progress rows, and `Source` column are all the site names
  (`linkedin`, `indeed`), never the backend library. If one site fails mid-call,
  the run retries the enabled sites individually for that search so the healthy
  site's rows are not lost, and an enabled site that returns zero rows across the
  whole run logs a warning. (#87)
- **BREAKING: `linkedin` and `indeed` are now top-level config sources**
  (was a single `sources.jobspy:` block with `linkedin` / `indeed` sub-flags and
  a nested `jobs:` query block). Each site now carries its own
  `results_wanted_per_query` and `hours_old`; the Indeed-only `country` knob
  replaces `country_indeed` (and lives only on `indeed`, the one site it
  affects). `jobs run -S jobspy` is gone — select `-S linkedin` and/or
  `-S indeed`. There is no compat shim: a config still carrying `sources.jobspy:`
  fails the normal config-validation check and must be rewritten. Before:

  ```yaml
  sources:
    jobspy:
      enabled: true
      linkedin: true
      indeed: true
      jobs:
        results_wanted_per_query: 50
        hours_old: 168
        country_indeed: USA
  ```

  After:

  ```yaml
  sources:
    linkedin:
      enabled: true
      results_wanted_per_query: 50
      hours_old: 168
    indeed:
      enabled: true
      results_wanted_per_query: 50
      hours_old: 168
      country: USA
  ```
  (#88)
- **Faster `jobs run`**: the Product and Fit/Notes enrichment phases now overlap
  under one shared concurrency cap (never more than `claude.max_parallel`
  provider calls in flight across both), detail-page fetches run on a small
  per-host-throttled pool instead of a single serial loop, the Apple scrape
  waits on the live search response instead of fixed multi-second sleeps, and
  the headless scrape pool default rose from 4 to 8 workers so all headless
  sources run in one wave. (#87)
- **HN "Who's Hiring" now surfaces up to 500 matching posts** (was 100): the
  default `hn_max_posts` cap was hitting on every run, so the same first 100
  relevance-ranked roles recurred and nothing past them was ever scraped.
  Raised to 500 (the thread fetch already pulls the whole thread).
- **`jobs run` now shows live progress instead of going silent**: in normal
  mode the run renders a phased live block — a `Scraping sources` group listing
  every source (each a per-source progress bar, pending -> running -> done), then
  an `Enriching jobs` group with detail / company-product / fit-and-notes
  counters — so a long run is visibly alive rather than looking hung. A
  per-source breakdown (`found / new / already in csv / skipped by location`)
  and a reconciling `Completed:` line print at the end. The live block renders
  on any interactive terminal and stays pinned; verbosity controls only how much
  scrolls above it (normal: warnings only; `-v`: INFO heartbeats and per-source
  timings; `-vv`: the full stream; `-q`: suppresses the display and every
  progress line, leaving only errors). Problems surface live above the block as
  they happen, with a terse end-of-run `Warnings: N (shown above)` line, instead of being
  held back to a section at the end. During the serial Apple phase the browser
  runs headless while the block is pinned so its window can't cut into the
  display. Non-interactive output (cron, launchd, pipes) falls back to plain
  lines with no ANSI. All human progress now goes to stderr, leaving stdout for
  the dry-run table and a future `--json`. (#71, #72)
- **`jobs run` live display rebuilt on enlighten** (no behaviour regression,
  fixes long-run stranding): a 2.5-hour run previously stranded ~700 stale
  copies of the block in the scrollback because Rich repainted the region on
  every write and ran a background refresh thread that raced the log stream.
  enlighten pins the bars in a terminal scroll region and lets the terminal
  scroll logs above them, with no per-line repaint and no refresh thread. Every
  source is now its own progress bar that fills as it works and stays pinned at
  its result (`linkedin  61 found`, red on failure), under a `Scraping sources`
  header bar with green (ok) / red (failed) segments. The per-row ticking elapsed
  timer is gone — a known-slow board (LinkedIn, Indeed, the headless Apple
  scrape) shows a one-time "can take several minutes" note until real progress
  arrives. An unresponsive terminal now falls back to plain-line mode on entry
  rather than stalling.
- **Live per-source progress for every board.** Sources used to sit silent
  during a scrape; now each advances its bar against its own natural unit —
  search term × country for LinkedIn/Indeed/Apple, boards for Greenhouse,
  categories for WeWorkRemotely, a single fetch for the rest. One uniform
  `ctx.report(done, total)` callback drives them all (no per-library log parsing,
  no extra requests, no background thread).
- **Finished source bars show a coloured outcome breakdown.** When a source
  completes, its bar re-colours into stacked segments — new (green), already in
  `jobs.csv` (magenta), skipped by location (yellow), and the duplicate/url-less
  remainder (grey) — so the result is readable at a glance, not just a count.
- **Generated `.dd-config.yaml` now surfaces every user-configurable setting**:
  the scaffold exposes the scraper transport knobs (`user_agent`, `timeout`,
  `search_terms`, `parallel_workers`, `max_pages`), the per-source knobs nested
  under each source (`wwr_categories`, `greenhouse_boards`, `hn_max_posts`, and
  the `linkedin` / `indeed` query knobs), and `ai.ollama.max_parallel` — all previously
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

- **`jobs status` per-state counts are now correct.** The status breakdown read a lowercase `status` column while `jobs.csv` uses the capitalized `Status`, so every row was bucketed as `unknown` and the applied/interviewing/etc. counts (and the awaiting-action total) were always wrong. (#99)
- **LLM scaffolding no longer leaks into the `Product/Purpose` cell.** When the model echoed the prompt's own list/label structure (`Line 1: ...`, a leading `1.`/`1)`/`-`/`*` bullet, or a `Product:`/`Purpose:` label), that prefix was stored verbatim; it is now stripped before the value is written. (#99)
- **A malformed salary in a RemoteOK listing no longer fails the whole source
  during `jobs run`**: a float or float-string pay value used to raise an error
  that dropped every RemoteOK result; the value is now coerced tolerantly and
  the listing is kept (with comp blank if it can't be parsed). (#83)
- **Job fit scores now appear in jobs.csv**: scores were computed by the
  enricher but silently dropped, leaving the Fit column blank after `jobs run`.
  Legacy `7/10`-style cells are still readable and normalize to bare integers on
  the next backfill. (#79)
- **Custom edits to `.claude/hooks` scripts now survive `doctor --fix` and
  version upgrades**: hook scripts previously were overwritten on every
  regenerate, bypassing the SHA-256 manifest contract every other managed file
  follows. They now join that contract — user-edited hooks are preserved (and
  counted as preserved), and hooks dropped from the package are reaped. Note:
  the first regenerate after this release refreshes hooks from the package once;
  edits made after that are preserved. (#78)
- `tracker update --extra` now merges keys into the existing extras instead of
  replacing them; unmentioned keys are no longer dropped. (#77)

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
