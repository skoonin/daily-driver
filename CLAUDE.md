# Daily Driver

Work planning and reporting system for SRE daily workflow.

## Purpose

This repo is the **engine** that powers daily work planning and end-of-day reporting. Configuration lives in `config.yaml`. Output (daily notes, plans) goes to the configured `output_dir`.

## Commands

### Daily Workflow
- `/day-start` - Morning planning: gathers calendar, Jira, RFCs, PRs, carry-forward, and helps plan the day
- `/day-end` - End of day: collects session data, compares plan vs actual, ticket sweep, writes daily notes
- `/check-in` - Mid-day review: re-reads plan, captures progress, flags overruns, reminds about ticket updates
- `/focus` - Toggle focus mode: suppresses check-in notifications for a set duration

### Reporting
- `/standup` - Generate async standup summary (Yesterday/Today/Blockers) to clipboard
- `/week-end` - Weekly rollup: aggregates daily notes into manager-friendly summary
- `/prep` - Meeting prep: pulls Jira/PR context relevant to an upcoming calendar meeting

### Setup
- `/setup` - One-time: verifies tool auth, configures workspace, checks automation status

## Architecture

- `config.yaml` - Central configuration (paths, repos, Jira instances, GitHub orgs, check-in settings, calendar sync)
- `scripts/` - Shell scripts for data gathering (calendar, Jira, RFCs, PRs, Claude sessions, git activity, carry-forward, ticket status)
- `agents/work-planner.md` - Planning intelligence agent (symlinked to `.claude/agents/`)
- `commands/` - Slash command definitions (symlinked to `.claude/commands/`)
- `context.md` - User work profile and preferences
- `launchd/` - macOS LaunchAgent that opens iTerm2 with claude /check-in when a check-in is due

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

- **Jira**: Configured instances via `acli` (see `config.yaml`), including RFC project tracking
- **GitHub**: Configured orgs via `gh` (see `config.yaml`)
- **Calendar**: macOS Calendar via `icalBuddy` (read) and AppleScript (write plan time blocks)
- **Claude Sessions**: `~/.claude/history.jsonl` and `sessions-index.json`
- **launchd**: Automated check-in triggers via macOS LaunchAgent (opens iTerm2 window)

## Output

- Daily plans and notes: `{output_dir}/YYYY/MM/YYYY-MM-DD-{plan,notes}.md`
- Weekly summaries: `{output_dir}/weekly/YYYY/YYYY-WNN-week.md`
- Plan files use YAML frontmatter for machine-readable structured data (carry-forward, plan items, status)
