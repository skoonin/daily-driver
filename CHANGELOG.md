# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] -- v2.0

### Added -- Job Search Refactor
- Jobs table (`jobs.md`), contacts log (`contacts.md`), location preferences (`location-preferences.md`) documentation in CLAUDE.md
- `/interview-prep` command for structured interview practice (behavioral, technical, system design)
- `make interview-prep` target with company/role argument support
- Personal task carry-forward extraction in `gather-carryforward.sh`
- Playwright MCP server allowed in settings.json

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
