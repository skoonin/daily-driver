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
- `/prep` - Meeting prep: pulls application context relevant to an upcoming calendar meeting (interviews, networking)

### Setup
- `/setup` - One-time: verifies tool installation, configures workspace, initializes tracker

## Architecture

- `config.yaml` - Central configuration (paths, repos, tracker settings, check-in times, calendar sync)
- `scripts/` - Shell scripts for data gathering (calendar, applications, Claude sessions, git activity, carry-forward)
- `scripts/tracker.sh` - Application tracker CRUD (add, update, list, stats, follow-ups)
- `agents/work-planner.md` - Planning intelligence agent (symlinked to `.claude/agents/`)
- `commands/` - Slash command definitions (symlinked to `.claude/commands/`)
- `context.md` - User profile and preferences
- `launchd/` - macOS LaunchAgent that opens iTerm2 with claude /check-in at fixed times

## Makefile

### Setup
- `make setup` - Install dependencies and configure environment
- `make deps` - Install script dependencies
- `make install` - Install symlinks and launchd plists
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

## Integrations

- **Application Tracker**: Local YAML file managed by `scripts/tracker.sh` using `yq`
- **Calendar**: macOS Calendar via `icalBuddy` (read) and AppleScript (write plan time blocks)
- **Claude Sessions**: `~/.claude/history.jsonl` and `sessions-index.json`
- **launchd**: Automated check-in triggers via macOS LaunchAgent (opens iTerm2 window)

## Output

- Daily plans and notes: `{output_dir}/YYYY/MM/YYYY-MM-DD-{plan,notes}.md`
- Weekly summaries: `{output_dir}/weekly/YYYY/YYYY-WNN-week.md`
- Application tracker: `{output_dir}/tracker.yaml`
- Plan files use YAML frontmatter for machine-readable structured data (carry-forward, plan items, status)
