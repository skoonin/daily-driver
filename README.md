# Daily Driver

Personal productivity assistant. A durable data layer for daily planning and tracking, with optional job search, and a Claude-powered conversational layer on top.

## What it does

Daily Driver keeps the unglamorous parts of a focused workweek out of your head and in structured files:

- **Workspace** — one directory holds plans, notes, tracker, config. `daily-driver init .` scaffolds it.
- **Tracker** — YAML-backed list of anything you follow up on (tasks, errands, project milestones, contacts, job applications). Categories are free-form: list one under `tracker.categories` to require fields on it, and attach your own fields with `--extra key=value`.
- **Day cycle** — `day-start`, `check-in`, and `day-end` open a Claude session that plans your day from the tracker, calendar, and recent commits, checks progress at mid-day, and winds the day down. `context.md` holds your background and recurring tasks.
- **Focus mode** — file-locked toggle that suppresses scheduled check-ins.
- **Status dashboard** — totals, stalled items, last week of activity as a Rich table (or JSON).
- **Summary and voice** — `summary` writes a period summary and copies it to the clipboard; `voice-update` builds `voice-profile.md` from your own writing so drafts sound like you.
- **Scheduler** — `scheduler install` sets up macOS launchd reminders for check-ins, and for day-start and day-end once you set their times. With job search on, it also schedules the job scrape and board discovery.
- **Job search (optional)** — multi-source scrape (RemoteOK, WeWorkRemotely, HN Who's Hiring, HN YC-funded jobs, Greenhouse, Ashby, Lever, Workable, Workday, LinkedIn/Indeed via JobSpy, Apple), with board auto-discovery (Greenhouse, Ashby, Lever), dedup, filter, and AI-driven enrichment via `claude` or a local Ollama model.
- **Gather modules** — typed readers for git history and macOS Calendar (icalBuddy).
- **Integrations** — thin subprocess bridges to `claude`, `pbcopy`, `launchctl`.

## Install

Requires Python 3.11+ and macOS. See [docs/install.md](docs/install.md) for the full prerequisites and Playwright notes.

```bash
pip install git+https://github.com/skoonin/daily-driver.git
daily-driver --version
```

## Quick start

```bash
daily-driver init ~/daily-driver-workspace
cd ~/daily-driver-workspace
daily-driver doctor
daily-driver tracker add --category task --title "Renew passport"
daily-driver status
```

Full first-day walkthrough in [docs/usage.md](docs/usage.md). Bare-minimum scaffold steps in [docs/quick-start.md](docs/quick-start.md).

## Job search quick start

The job-search plugin scrapes a configurable set of boards into `jobs.csv`, dedupes against what's already there, and enriches each row (fit, notes, comp) via `claude` or a local Ollama model. It is off by default.

1. Switch job search on. For a new workspace, run `daily-driver init --job-search ~/daily-driver-workspace`. For an existing workspace, add a `plugins.job_search` block to `.dd-config.yaml` and run `daily-driver doctor --fix`. If you use the scheduler, run `daily-driver scheduler install` afterwards to add the `jobs` and `jobs-discover` agents.

2. Turn the scraper on with `scraper.enabled: true` and list your sources. If `init --job-search` already wrote a `plugins.job_search` block, merge these settings into it; a second `plugins:` key replaces the first. Listing a source key enables it (disable with `enabled: false`):

```yaml
plugins:
  job_search:
    persona: "Senior SRE, IC-track only"
    seniority_keywords: [senior, staff, principal]
    locations:
      remote: true
      # ISO code -> city allow-list; [] = whole country, cities = only those
      countries: {US: [], CA: [Vancouver, Victoria]}
    scraper:
      enabled: true
    sources:
      greenhouse: {enabled: true, greenhouse_boards: [company-a, company-b]}
      ashby:      {enabled: true}   # boards come from discover-boards below
      lever:      {enabled: true}   # ditto
      remoteok:   {remoteok_tags: [devops, kubernetes, aws]}
      hn_who_is_hiring: {}
```

3. Discover which Greenhouse / Ashby / Lever boards list your roles. This sweeps the ATS slug universe and caches the matches for every later `jobs run` — the first sweep probes everything and takes tens of minutes; later sweeps are incremental: new slugs, plus boards overdue for re-probe, up to `discovery.max_reprobe_per_sweep` per platform per sweep:

```bash
daily-driver jobs discover-boards
```

4. Run the search, then top up any cells a run left empty:

```bash
daily-driver jobs run           # scrape + enrich -> jobs.csv
daily-driver jobs backfill      # re-enrich empty cells from an earlier run
daily-driver jobs status        # last-run metadata + row counts
```

Full field reference in [configuration.md](docs/configuration.md#pluginsjob_search); the complete command set, source list, and AI-provider choice in [usage.md](docs/usage.md#jobs).

## Workspace layout

```
<workspace>/
  .dd-config.yaml          # workspace marker + structured config
  context.md               # your background and recurring tasks (seeded once, edit freely)
  voice-profile.md         # writing voice (ditto)
  README.md                # command reference for this workspace (package-managed)
  .gitignore               # seeded once, yours after that
  tracker.yaml             # tracker entries (CLI-managed)
  jobs.csv                 # scraper output (job search only)
  .daily-driver/
    version                # version stamp
    manifest.json          # SHA-256 manifest of managed files
    state/                 # ephemeral: locks, logs
  .claude/
    settings.local.json    # Claude settings (shipped blocks refreshed, your top-level keys kept)
    hooks/                 # shipped hooks (regenerated on drift)
    commands/daily-driver/ # shipped slash commands (regenerated on drift)
    agents/daily-driver/   # shipped agents (ditto)
    commands/user/         # your custom commands (untouched)
    agents/user/           # your custom agents (untouched)
    commands/*.md          # your custom commands (untouched)
    agents/*.md            # your custom agents (untouched)
  <YYYY>/<MM>/             # daily plans + notes
```

`tracker.yaml`, `jobs.csv`, and the `<YYYY>/<MM>/` directories live in `output_dir` (default `.`, the workspace directory). `context.md` and `voice-profile.md` always live in the workspace directory.

Workspace discovery walks up from CWD looking for `.dd-config.yaml`. Override with `--workspace PATH`.

## Documentation

Full index: [docs/README.md](docs/README.md).

**Users** — start with [quick-start.md](docs/quick-start.md) to scaffold, then [usage.md](docs/usage.md) for the daily flow. Read [concepts.md](docs/concepts.md) once for the mental model.

**Developers** — start with [docs/dev/developer.md](docs/dev/developer.md).

| Doc | What it covers |
| --- | --- |
| [install.md](docs/install.md) | Prerequisites, upgrade, and Playwright setup |
| [concepts.md](docs/concepts.md) | Mental model: workspace layout, surfaces, design decisions |
| [quick-start.md](docs/quick-start.md) | Minimal scaffold checklist |
| [usage.md](docs/usage.md) | End-to-end daily flow with worked examples |
| [commands.md](docs/commands.md) | Every subcommand and non-obvious flags |
| [cli-tree.md](docs/cli-tree.md) | At-a-glance command tree |
| [configuration.md](docs/configuration.md) | `.dd-config.yaml` schema and customization |
| [ollama-setup.md](docs/ollama-setup.md) | Local LLM provider for enrichment/summary |
| [troubleshooting.md](docs/troubleshooting.md) | Failure modes and recovery |
| [dev/developer.md](docs/dev/developer.md) | Architecture, runtime flow, init contract |
| [dev/extending.md](docs/dev/extending.md) | Adding subcommands, plugins, and scraper sources |
| [dev/releasing.md](docs/dev/releasing.md) | Release workflow, semver, CHANGELOG |

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup and commit conventions.

## Platform

macOS only. `pbcopy`, `icalBuddy`, AppleScript, and `launchd` integrations assume macOS. Linux is on the roadmap.

## License

Apache License 2.0. See [LICENSE](LICENSE).
