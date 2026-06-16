# Daily Driver

Personal productivity assistant. A durable data layer for daily planning, tracking, and job search with a Claude-powered conversational layer on top.

## What it does

Daily Driver keeps the unglamorous parts of a focused workweek out of your head and in structured files:

- **Workspace** — one directory holds plans, notes, tracker, config. `daily-driver init .` scaffolds it.
- **Tracker** — YAML-backed list of anything you follow up on (applications, tasks, chores, contacts). Categories are config-driven and extensible via `--extra key=value`.
- **Focus mode** — file-locked toggle that suppresses scheduled check-ins.
- **Status dashboard** — totals, stalled items, last week of activity as a Rich table (or JSON).
- **Job-board scraper** — multi-source scrape (RemoteOK, WeWorkRemotely, HN Who's Hiring, HN YC-funded jobs, Greenhouse, JobSpy for LinkedIn/Indeed, Apple), dedup, filter, and AI-driven enrichment via `claude` or a local Ollama model.
- **Gather modules** — typed readers for git history and macOS Calendar (icalBuddy).
- **Integrations** — thin subprocess bridges to `claude`, `pbcopy`, `launchctl`.

## Install

Requires Python 3.11+ and macOS. See [docs/install.md](docs/install.md) for the full prerequisites and Playwright notes.

```bash
pip install 'git+https://github.com/skoonin/daily-driver.git@v0.2.0'
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

Full first-day walkthrough in [docs/usage.md](docs/usage.md). Bare-minimum scaffold steps in [docs/quick-start.md](docs/quick-start.md).

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

Full index: [docs/README.md](docs/README.md).

**Users** — start with [quick-start.md](docs/quick-start.md) to scaffold, then [usage.md](docs/usage.md) for the daily flow. Read [concepts.md](docs/concepts.md) once for the mental model.

**Developers** — start with [docs/dev/developer.md](docs/dev/developer.md).

| Doc | What it covers |
| --- | --- |
| [concepts.md](docs/concepts.md) | Mental model: workspace layout, surfaces, design decisions |
| [quick-start.md](docs/quick-start.md) | Minimal scaffold checklist |
| [usage.md](docs/usage.md) | End-to-end daily flow with worked examples |
| [commands.md](docs/commands.md) | Every subcommand and non-obvious flags |
| [configuration.md](docs/configuration.md) | `.dd-config.yaml` schema and customization |
| [ollama-setup.md](docs/ollama-setup.md) | Local LLM provider for enrichment/summary |
| [troubleshooting.md](docs/troubleshooting.md) | Failure modes and recovery |
| [dev/developer.md](docs/dev/developer.md) | Architecture, runtime flow, init contract |
| [dev/extending.md](docs/dev/extending.md) | Adding subcommands and scraper sources |
| [dev/releasing.md](docs/dev/releasing.md) | Release workflow, semver, CHANGELOG |

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup and commit conventions.

## Platform

macOS only for v0.2.0. `pbcopy`, `icalBuddy`, AppleScript, and `launchd` integrations assume macOS. Linux is on the roadmap.

## License

MIT.
