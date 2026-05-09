# Daily Driver

ADHD-aware personal productivity assistant. A durable data layer for daily planning, tracking, and job search with a Claude-powered conversational layer on top.

> **Status: v0.1.0.** Pure-Python CLI installable from source (macOS arm64).

## What it does

Daily Driver keeps the unglamorous parts of a focused workweek out of your head and in structured files:

- **Workspace** — one directory holds plans, notes, tracker, config. `daily-driver init .` scaffolds it.
- **Tracker** — YAML-backed list of anything you follow up on (applications, tasks, chores, contacts). Categories are config-driven and extensible via `--extra key=value`.
- **Focus mode** — file-locked toggle that suppresses scheduled check-ins.
- **Status dashboard** — totals, stalled items, last week of activity as a Rich table (or JSON).
- **Job-board scraper** — multi-source scrape (RemoteOK, WeWorkRemotely, HN Who's Hiring, Greenhouse, JobSpy for LinkedIn/Indeed/Glassdoor/Google, Wellfound, Apple), dedup, filter, and Claude-CLI enrichment.
- **Gather modules** — typed readers for git history, macOS Calendar (icalBuddy), Claude Code sessions, daily notes.
- **Integrations** — thin subprocess bridges to `claude`, `pbcopy`, `launchctl`.

## Install

Requires Python 3.11+ and macOS. See [docs/install.md](docs/install.md) for the full prerequisites and Playwright notes.

```bash
pip install 'git+https://github.com/skoonin/daily-driver.git@v0.1.0'
daily-driver --version
```

## Quick start

```bash
daily-driver init ~/daily-driver-workspace
cd ~/daily-driver-workspace
daily-driver doctor
daily-driver tracker add --category task --title "Write phase 7 tests"
daily-driver status
```

Full walkthrough in [docs/quick-start.md](docs/quick-start.md).

## Workspace layout

```
<workspace>/
  .dd-config.yaml          # workspace marker + structured config
  context.md               # your profile (seeded once, edit freely)
  voice-profile.md         # writing voice (ditto)
  tracker.yaml             # tracker entries (CLI-managed)
  jobs.csv                 # scraper output (plugin-managed)
  .daily-driver/
    version                # version stamp
    manifest.json          # SHA-256 manifest of managed .claude/ files
    state/                 # ephemeral: locks, logs
  .claude/
    commands/daily-driver/ # shipped slash commands (regenerated on drift)
    agents/daily-driver/   # shipped agents (ditto)
    commands/*.md          # your custom commands (untouched)
    agents/*.md            # your custom agents (untouched)
  <YYYY>/<MM>/             # daily plans + notes
```

Workspace discovery walks up from CWD looking for `.dd-config.yaml`. Override with `--workspace PATH`.

## Documentation

| Doc | Audience |
|-----|----------|
| [install.md](docs/install.md) | Install, upgrade, Playwright |
| [quick-start.md](docs/quick-start.md) | First-run walkthrough |
| [commands.md](docs/commands.md) | All subcommands and non-obvious flags |
| [configuration.md](docs/configuration.md) | `.dd-config.yaml` reference |
| [customization.md](docs/customization.md) | `.claude/` ownership, custom commands/agents |
| [troubleshooting.md](docs/troubleshooting.md) | doctor errors, launchd, flock, Playwright |
| [developer.md](docs/developer.md) | Architecture, runtime flow, generate, init contract |
| [extending.md](docs/extending.md) | Adding subcommands and scraper sources |
| [releasing.md](docs/releasing.md) | Release workflow, semver, CHANGELOG |

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup and commit conventions.

## Platform

macOS only for v0.1.0. `pbcopy`, `icalBuddy`, AppleScript, and `launchd` integrations assume macOS. Linux is on the roadmap.

## License

MIT.
