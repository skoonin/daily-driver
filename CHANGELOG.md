# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
