# Daily Driver

Work planning and reporting system for SRE daily workflow.

## Purpose

This repo is the **engine** that powers daily work planning and end-of-day reporting. Output (daily notes, plans) goes to `~/git/daily-notes/`.

## Commands

- `/day-start` - Morning planning: gathers calendar, Jira, PRs, and helps plan the day
- `/day-end` - End of day: collects session data, compares plan vs actual, writes daily notes
- `/setup` - One-time: verifies tool auth, configures context.md

## Architecture

- `scripts/` - Shell scripts for data gathering (calendar, Jira, PRs, Claude sessions, git activity)
- `.claude/agents/work-planner.md` - Planning intelligence agent
- `.claude/commands/` - Slash command definitions
- `context.md` - User work profile and preferences

## Integrations

- **Jira**: Two instances via `acli` (corescientific.com/IM, core-hpc/SRE)
- **GitHub**: Two orgs via `gh` (corescientific, core-hpc)
- **Calendar**: macOS Calendar via `icalBuddy`
- **Claude Sessions**: `~/.claude/history.jsonl` and `sessions-index.json`

## Output

Daily notes saved to `~/git/daily-notes/YYYY/MM/YYYY-MM-DD-{plan,notes}.md`
