# Daily Driver

Job search planning and daily accountability system.

## Purpose

This repo is the **engine** that powers daily job search planning and end-of-day reporting. Configuration lives in `config.yaml`. Output (daily notes, plans) goes to the configured `output_dir`. Applications are tracked in `{output_dir}/tracker.yaml`.

## Commands

### Daily Workflow
- `/day-start` - Morning planning: gathers calendar, applications, carry-forward, and helps plan the day
- `/day-end` - End of day: collects session data, compares plan vs actual, application follow-ups, writes daily notes
- `/check-in` - Mid-day review: re-reads plan, captures progress, flags overruns, reminds about follow-ups
- `/focus` - Toggle focus mode: suppresses check-in notifications for a set duration

### Reporting
- `/standup` - Generate async standup summary (Yesterday/Today/Blockers) to clipboard
- `/week-end` - Weekly rollup: aggregates daily notes into weekly summary
- `/month-end` - Monthly rollup: summarize the month's weekly summaries and daily notes into a monthly report
- `/prep` - Meeting prep: pulls application context relevant to an upcoming calendar meeting (interviews, networking)
- `/interview-prep` - Interview preparation: practice questions and gather role context for an upcoming interview

### Writing
- `/voice-update` - Update the writing voice profile from an approved sample or explicit feedback

### Setup
- `/setup` - One-time: verifies tool installation, configures workspace, initializes tracker

## Architecture

- `config.yaml` - Central configuration (paths, repos, tracker settings, check-in times, calendar sync)
- `scripts/` - Shell scripts for data gathering (calendar, applications, Claude sessions, git activity, carry-forward)
- `scripts/tracker.sh` - Application tracker CRUD (add, update, list, stats, follow-ups)
- `scripts/scrape-jobs.py` - Job scraper: 8 sources (LinkedIn, Indeed, Apple, Greenhouse, RemoteOK, WWR, HN, Wellfound), detail-page enrichment, Claude CLI enrichment (Fit, GD Rating, Product/Purpose, Notes)
- `scripts/gather-jobs.sh` - Orchestrator: builds job queue from config, runs scraper, triggers enrichment
- `agents/work-planner.md` - Planning intelligence agent (symlinked to `.claude/agents/`)
- `commands/` - Slash command definitions (symlinked to `.claude/commands/`)
- `context.md` - User profile and preferences
- `launchd/` - macOS LaunchAgent that opens iTerm2 with claude /check-in at fixed times

## Scripts vs. inline commands

**Rule**: Non-trivial shell logic lives in `scripts/`, not inline in `commands/*.md`. Even one-line diagnostic helpers (e.g. `check-state-dir.sh`, `check-output-dir.sh`) belong as scripts.

**Why**: Claude Code's permission model prompts the user to approve each distinct shell invocation. A named script is approved once and reused; a fresh inline command string triggers a new approval prompt every run, which is disruptive during a `/setup` or `/day-start` that runs many checks. Scripts also let us audit, test, and version the logic independently of the prompt.

**How to apply**: When adding a new command step that runs shell, prefer `bash scripts/foo.sh` over inline `yq ... | jq ...`. "But it's only one line" is not a reason to inline — the permission friction cost is per-invocation, not per-line.

## Makefile

### Setup
- `make setup` - Install dependencies and configure environment
- `make deps` - Install script dependencies
- `make install` - Install symlinks (commands, agents, settings.json). LaunchAgents are installed separately via `make launchd-install`.
- `make status` - Show automation and integration status

### Automation
- `make launchd-install` - Install the check-in LaunchAgent plist
- `make launchd-start` - Load and start the LaunchAgent

### Workflow
Workflow targets invoke `claude` with `--agent work-planner` and `-n` for session naming. `standup` and `focus` run headless via `-p`.

- `make day-start` - Run /day-start planning session
- `make day-end` - Run /day-end notes session
- `make check-in` - Run /check-in mid-day review
- `make standup` - Generate standup summary (headless)
- `make week-end` - Run /week-end rollup session
- `make prep` - Run /prep meeting prep session
- `make focus` - Toggle focus mode (headless)
- `make voice-update ARGS="/path/to/file.md"` - Update voice profile from a writing sample

### Job Scraping
- `make gather-jobs` - Run full job discovery pipeline (queue + scrape + enrich)
- `make scrape-jobs` - Run scraper only (usage: `make scrape-jobs ARGS="--dry-run"`)
- `make backfill-jobs` - Re-enrich empty Fit/GD/Product/Notes in existing jobs.csv rows

## Integrations

- **Application Tracker**: Local YAML file managed by `scripts/tracker.sh` using `yq`
- **Calendar**: macOS Calendar via `icalBuddy` (read) and AppleScript (write plan time blocks)
- **Claude Sessions**: `~/.claude/history.jsonl` and `sessions-index.json`
- **Job Scraper**: Python scraper (`scripts/scrape-jobs.py`) with 8 sources. Phase 1 (headless, parallel): RemoteOK, WWR, HN, Greenhouse API, Apple. Phase 2 (visible browser, serial): LinkedIn, Indeed, Wellfound. Pagination via `max_pages` config. Detail-page enrichment for comp data. Claude CLI enrichment for Fit, GD Rating, Product/Purpose, Notes. Backfill mode re-enriches existing rows.
- **launchd**: Automated check-in triggers and scheduled job scraping via macOS LaunchAgents (opens iTerm2 window)

## Output

- Daily plans and notes: `{output_dir}/YYYY/MM/YYYY-MM-DD-{plan,notes}.md`
- Weekly summaries: `{output_dir}/weekly/YYYY/YYYY-WNN-week.md`
- Application tracker: `{output_dir}/tracker.yaml`
- Jobs discovered: `{output_dir}/jobs.csv` (populated by `make gather-jobs`; backfill gaps with `make backfill-jobs`)
- Job queue (daily): `{output_dir}/job-queue-YYYY-MM-DD.md`
- Company docs: `{output_dir}/companies/{company}/{app-id}-{role-slug}.md` (auto-created by `tracker.sh add`)
- Contacts log: `{output_dir}/contacts.csv`
- Location preferences: `{output_dir}/location-preferences.md`
- Writing voice profile: `{output_dir}/voice-profile.md`
- Plan files use YAML frontmatter for machine-readable structured data (carry-forward, plan items, status)

## Jobs Table (`jobs.csv`)

Tracks every role discovered during the search -- applied, skipped, or still evaluating. This is the top of the funnel. Active application pipeline details (stages, follow-ups, dates) live in `tracker.yaml`.

**Columns**: Status, Company, Product/Purpose, Role, Comp, Location, Fit (1-10), GD Rating, Source, Date Found, Date Applied, Link, Notes

**Status values**: `found` (just discovered), `researched` (details reviewed), `applied`, `skipped` (with reason in Notes), `rejected`, `ghosted`, `interviewing`, `offer`, `withdrawn`

**Maintenance rules**:
- Add every role discovered during research, even if immediately skipped (builds pattern data on what's out there)
- Always include company product/purpose -- never leave blank
- When applying, update Status to `applied`, set Date Applied, and ensure the role also exists in `tracker.yaml`
- Source tracks where the role was found (HN Who's Hiring, LinkedIn, referral, company careers page, etc.)

## Contacts Log (`contacts.csv`)

Tracks people met during the search: recruiters, hiring managers, referrals, networking contacts.

**Columns**: #, Name, Company, Role/Title, How Met, Date, Last Contact, Next Action, Notes

**Maintenance rules**:
- Add anyone worth remembering after a meaningful interaction (not every LinkedIn connection)
- Update Last Contact and Next Action after each interaction
- Include context in Notes that future-you needs (what you discussed, what they offered to help with)

## Company Docs (`companies/{company}/{app-id}-{role-slug}.md`)

Living documents that track everything about a specific application: company research, call notes, interview rounds, questions, and assessment. One file per application.

**Structure**: `{output_dir}/companies/{company-slug}/{app-id}-{role-slug}.md`

**Auto-created**: `tracker.sh add` creates a template doc when a new application is added.

**Maintenance rules**:
- Append to the Timeline section after every interaction (call, email, interview round)
- Update Company and Role sections as you learn more
- Keep Open Questions current -- remove answered ones, add new ones
- Record assessment and decision rationale -- future-you needs to know why you moved forward or didn't

## Location Preferences (`location-preferences.md`)

Ranked location tiers that drive job filtering. Read this file before assessing new roles to apply location fit correctly. Update when preferences change or new locations are evaluated.

## Writing Voice Profile (`voice-profile.md`)

Captures Shawn's writing style for cover letters, application responses, and professional communications. The profile documents language patterns, structural conventions, things to avoid, and approved writing samples with annotations.

**Before drafting any professional communication**: read `{output_dir}/voice-profile.md` and apply its patterns. The profile is the source of truth for tone and style -- do not fall back to generic professional writing conventions for dimensions the profile covers.

**Keeping it current**: run `/voice-update` after any approved writing sample to extract new patterns. The command reads the current profile, analyzes the sample, proposes additions, and updates only after confirmation.

**Update log**: the profile file maintains an update log table at the bottom tracking what changed, when, and from what source.
