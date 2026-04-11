# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] -- v2.0

### Fixed -- Audit follow-up (reliability + simplification)
- `tracker.sh audit`: exact company-name match replaces substring glob; "Stripe" no longer false-matches "Stripe Financial"
- `scrape-jobs.py`: `enrich_company_descriptions` now honors `job_search.scraper.max_enrich_companies` budget (default 10) and 15s timeout; prevents runaway Claude calls
- `scrape-jobs.py`: HN scraper catches `requests.RequestException` and returns `[]` instead of crashing the run
- `gather-sessions.sh`: crash-detection `set -e` escape fixed (jq array-diff replaces piped `while read` subshell)
- `calendar-sync.sh`: surfaces AppleScript errors with Privacy > Calendars hint instead of silently swallowing
- `checkin-state.sh`: state writes go through `write_state` helper with `.tmp + mv` atomic pattern
- Consolidated `commit-{daily-notes,weekly,monthly}.sh` into single `commit-notes.sh {daily|weekly|monthly}`

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
