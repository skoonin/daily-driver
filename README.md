# Daily Driver

Work planning and reporting system for SRE daily workflow, powered by [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

## What It Does

Daily Driver provides Claude Code slash commands for structured daily work management:

### Daily Workflow
- **`/day-start`** -- Morning planning: syncs repos, gathers calendar/Jira/RFC/PR data, reviews carry-forward from yesterday, asks about personal tasks, and produces a time-blocked plan with YAML frontmatter
- **`/day-end`** -- End of day: collects session data and git activity, runs ticket status sweep, compares plan vs actual, builds carry-forward for tomorrow, writes daily notes, and auto-commits
- **`/check-in`** -- Mid-day review: re-reads morning plan, captures progress on planned items, flags overruns (task running 2x longer than planned), reminds about Jira ticket status updates
- **`/focus`** -- Toggle focus mode: suppresses check-in triggers for a set duration

### Reporting
- **`/standup`** -- Generate async standup summary (Yesterday/Today/Blockers) and copy to clipboard for Slack
- **`/week-end`** -- Weekly rollup: aggregates the week's daily notes into a manager-friendly summary
- **`/prep`** -- Meeting prep: pulls Jira/PR context relevant to an upcoming calendar meeting

### Setup
- **`/setup`** -- One-time: verifies tool installation, checks authentication, validates configuration

All configuration lives in `config.yaml`.

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| `claude` | Claude Code CLI | [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code) |
| `acli` | Atlassian CLI for Jira | [acli docs](https://acli.dev) |
| `gh` | GitHub CLI | `brew install gh` |
| `icalBuddy` | macOS Calendar access | `brew install ical-buddy` |
| `jq` | JSON processing | `brew install jq` |
| `yq` | YAML processing | `brew install yq` |
| `terminal-notifier` | Fallback notifications (optional) | `brew install terminal-notifier` |

## Setup

```bash
git clone <repo-url> ~/git/daily-driver
cd ~/git/daily-driver
make setup
```

`make setup` runs `make deps` (installs all dependencies), `make install` (symlinks commands and agents into `.claude/`), and verifies tool authentication. Edit `config.yaml` to match your environment after cloning.

Run `make status` at any time to check installation state.

## Configuration

All settings are in `config.yaml`. Key sections:

- **output_dir** -- Where plans and notes are saved
- **sync_repos** -- Repos to pull at day-start
- **jira** -- Jira instances with project keys, RFC projects, and ticket patterns
- **github_orgs** -- GitHub organizations for PR queries
- **calendar** -- macOS Calendar sync settings (calendar name, enable/disable)
- **checkin** -- Automated check-in interval, work hours for iTerm2 check-in triggers
- **planning** -- Carry-forward stale threshold
- **reporting** -- Standup format (slack/plain), weekly save directory

## Usage

### Make targets (primary workflow)

Run from any terminal without an active Claude Code session:

```bash
make day-start    # Morning planning
make check-in     # Mid-day review
make day-end      # EOD review and notes
make standup      # Headless standup -- copies result to clipboard
make focus ARGS="90 SRE-123"  # Focus mode for 90 minutes, linked to ticket
make help         # Full target list
```

### Slash commands (inside Claude Code session)

```
/day-start    /day-end    /check-in
/standup      /week-end   /prep       /focus
```

## Plan File Format

Plan and notes files use YAML frontmatter for structured data (carry-forward items, plan items with status, personal tasks) followed by a human-readable markdown body. This allows machine parsing for features like calendar sync, check-in, and reporting while keeping files readable.

## Output

```
{output_dir}/
  2026/
    03/
      2026-03-31-plan.md       # Morning plan (frontmatter + markdown)
      2026-03-31-notes.md      # EOD notes (frontmatter + markdown)
  weekly/
    2026/
      2026-W14-week.md         # Weekly rollup
```

## Project Structure

```
daily-driver/
  config.yaml                  # Central configuration
  context.md                   # User work profile and preferences
  CLAUDE.md                    # Claude Code project instructions
  Makefile                     # deps, install, status, launchd, and workflow targets
  commands/
    day-start.md               # Morning planning workflow
    day-end.md                 # EOD review workflow
    check-in.md                # Mid-day check-in
    focus.md                   # Focus mode toggle
    standup.md                 # Async standup generator
    week-end.md                # Weekly rollup
    prep.md                    # Meeting prep
    setup.md                   # One-time setup verification
  agents/
    work-planner.md            # Planning behavior instructions
  scripts/
    gather-calendar.sh         # macOS Calendar via icalBuddy
    gather-jira.sh             # Jira tickets via acli
    gather-rfcs.sh             # RFC project queries via acli
    gather-prs.sh              # GitHub PRs via gh
    gather-sessions.sh         # Claude Code session summaries
    gather-git-activity.sh     # Today's git commits
    gather-carryforward.sh     # Structured carry-forward from yesterday
    gather-notes-range.sh      # Read notes/plans for a date range
    gather-ticket-status.sh    # Bulk Jira ticket status lookup
    extract-jira-refs.sh       # Extract ticket keys from text
    calendar-sync.sh           # Write plan time blocks to macOS Calendar
    sync-repos.sh              # Pull tracked repos
    focus-mode.sh              # Focus lock file management
    check-in-notify.sh         # launchd trigger: opens iTerm2 window with claude /check-in (falls back to Terminal.app)
    checkin-state.sh           # Runtime state management
    launchd-install.sh         # LaunchAgent install/uninstall
    launch-day-end.sh          # iTerm2 automation for EOD trigger
  launchd/
    com.daily-driver.checkin.plist  # LaunchAgent template
```

## Platform

macOS only. Relies on macOS-specific tools (icalBuddy, BSD date, AppleScript, launchd, osascript).
