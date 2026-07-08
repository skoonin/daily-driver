# Using Daily Driver

End-to-end guide to the daily flow and the surfaces you touch most: tracker, focus, jobs, day-cycle launchers, summary, voice, and scheduler.

- Mental model and workspace layout: [concepts.md](concepts.md)
- Installation: [install.md](install.md)
- Config syntax: [configuration.md](configuration.md)
- Per-flag detail: [commands.md](commands.md)

## First day

A walkthrough you can run end-to-end the first time.

### 1. Scaffold the workspace

```bash
daily-driver init ~/daily-driver-workspace
cd ~/daily-driver-workspace
daily-driver doctor
```

`init` is idempotent — re-running tops up missing files and prints a `Created: ... ; Skipped: ...` summary. It never clobbers `context.md` or `voice-profile.md`. `-f` / `--force` resets `.dd-config.yaml` (the old copy is kept as `.dd-config.yaml.bak`).

`doctor` checks contracts and dependencies. WARNINGs exit 0; ERRORs need `doctor --fix` (regenerates managed files, restores deleted ones) or `doctor --reset` (full overwrite).

### 2. Fill in `context.md`

Replace the seeded placeholders with real biographical context — what you do, what you're working on, anything you'd tell a new collaborator. `claude` reads this before every session, so a richer file means better conversations.

`voice-profile.md` can wait until you have writing samples — see [Voice updates](#voice-updates).

### 3. Add a first tracker entry

```bash
daily-driver tracker add -c task -T "draft Q2 OKRs" -s open
daily-driver tracker list
daily-driver status
```

`status` is the dashboard: setup gaps first (if any), totals by category and status, stalled items, and seven days of activity.

### 4. Start the day

```bash
daily-driver day-start
```

Spawns a `claude` session with the `work-planner` agent and your workspace attached, then runs `/daily-driver:day-start` — it reads your tracker, recent commits, and calendar, and helps draft the day's plan into `<workspace>/<YYYY>/<MM>/<DD>/plan.md`.

### 5. Check in at mid-day

```bash
daily-driver check-in
```

Resumes the morning session by default (set `claude.resume_check_in: false` to start fresh). Update the tracker as you go — via the slash command, or directly with `daily-driver tracker update <ID> -s in-progress`.

### 6. Wind down

```bash
daily-driver day-end
```

Reviews what got done, what didn't, and what to carry forward. Appends to the day's `notes.md`. That's the loop — repeat tomorrow.

## Tracker

The load-bearing surface. Categories are config-driven — edit `tracker.categories` in `.dd-config.yaml` to add `job`, `errand`, `contact`, `ticket`, or anything else.

| Command | Purpose |
|---|---|
| `tracker add -c CAT -T TEXT [...]` | Create an entry |
| `tracker list [-c CAT] [-s STATUS] [-t TAG] [--since SPEC] [-j]` | Filter; table or JSON |
| `tracker show ID [-j]` | Single-entry detail |
| `tracker update ID [-s STATUS] [-N NOTE] [-t TAGS] [...]` | Update; `-N` appends, `-t` replaces |
| `tracker delete ID` | Remove one entry |
| `tracker prune [-c CAT] [-s STATUS] [--older-than SPEC] [-n]` | Bulk delete (one filter required) |
| `tracker follow-ups [--overdue] [-j]` | Entries with `next_action` set |
| `tracker stats [-j]` | Counts by category and status |

```bash
daily-driver tracker add -c job -T "Senior SRE at Acme" -s open -l https://acme.example/jobs/123 --extra company=Acme --extra source=linkedin
daily-driver tracker update abc123 -s blocked -N "waiting on referral"
daily-driver tracker prune -c job -s dropped --older-than month -n   # dry-run first
```

### Statuses

Recommended set: `open`, `in-progress`, `blocked`, `done`, `ruled-out`. Anything else works; the CLI nudges once (to stderr) for a status outside that set that no other entry uses. Silence it with `tracker.warn_unknown_status: false`. Run `daily-driver help statuses` for the current recommended + in-use list.

### Date specifiers

`--since` (forward-looking) and `--older-than` (backward-looking) accept:

```
today | yesterday | tomorrow
week | month | quarter | year
Nd | Nw | Nm | Ny           (e.g. 30d, 6w)
YYYY-MM-DD
```

`daily-driver help dates` prints the same grammar at runtime.

## Focus mode

A flock-backed toggle other commands respect — scheduled check-ins skip while focus is on.

```bash
daily-driver focus on --for 90m --reason "deep work on Q2 OKRs"
daily-driver focus status
daily-driver focus off
```

`--for` accepts `30m`, `2h`, `1h30m`, or bare minutes; omitting it falls back to `focus.default_duration` (default `25m`). The lock lives at `.daily-driver/state/focus.lock` — if it sticks, `focus off` releases it, or delete the file directly.

## Day cycle launchers

Three thin wrappers around `claude` that spawn a workspace-aware session with the `work-planner` agent.

| Command | Slash command | Typical time |
|---|---|---|
| `day-start` | `/daily-driver:day-start` | morning |
| `check-in` | `/daily-driver:check-in` | midday |
| `day-end` | `/daily-driver:day-end` | evening |

Shared flags: `--session-name NAME`, `--agent NAME` (default `work-planner`), `--model {sonnet,opus,haiku}`. `check-in` adds `--no-resume` to force a fresh session; the default is governed by `claude.resume_check_in`.

### In-session slash commands

These ship to `.claude/commands/daily-driver/` but are not CLI subcommands — invoke them inside a running `claude` session.

| Slash command | What it does |
|---|---|
| `/daily-driver:daily-learning` | 15-30 minute learning drill (behavioral STAR, technical fundamentals, system design, etc.). Rotates topics by day of week, avoids recent repeats, appends to `<output>/interview-practice/<date>.md`. Offered as an opt-in step inside `/daily-driver:day-start`, or run standalone. |

## Jobs

The job-search plugin scrapes a configurable set of boards, dedupes against `jobs.csv`, and enriches missing fields (fit, notes, comp) via an AI provider.

### Enable it

Add a `plugins.job_search` block and set `scraper.enabled: true`. Minimal:

```yaml
plugins:
  job_search:
    persona: "Senior SRE, IC-track only"
    seniority_keywords: [senior, staff, principal]
    locations:
      home_city: Vancouver, BC
      remote: true
      # ISO code -> city allow-list; [] = whole country, cities = only those
      countries: {US: [], CA: [Vancouver, Victoria]}
    scraper:
      enabled: true
    sources:
      greenhouse:
        enabled: true
        greenhouse_boards: [anthropic, stripe, figma]
      hn_jobs: {}
      hn_who_is_hiring: {}
      remoteok: {remoteok_tags: [devops, kubernetes, aws]}
      weworkremotely: {}
```

Full field reference: [configuration.md](configuration.md#pluginsjob_search).

### Sources

| Source | Notes |
|---|---|
| `remoteok` | RemoteOK feed; `?tags=` slugs from `remoteok_tags` |
| `weworkremotely` | WeWorkRemotely (category-aware) |
| `hn_who_is_hiring` | Hacker News monthly thread |
| `hn_jobs` | YC-funded jobs |
| `greenhouse` | One row per `greenhouse_boards` entry |
| `ashby` | One row per `ashby_boards` entry (AshbyHQ public API) |
| `lever` | One row per `lever_boards` entry (Lever Postings API) |
| `workable` | One row per `workable_accounts` entry (Workable widget API) |
| `workday` | Paginated Workday careers site; one `workday_boards` entry per company |
| `linkedin` | LinkedIn via JobSpy |
| `indeed` | Indeed via JobSpy |
| `apple` | Apple careers (requires Playwright) |

`linkedin` and `indeed` are site-named sources (fetched via the `python-jobspy` library, an implementation detail). When both are enabled, each is fetched separately — its own progress row, retry, and failure isolation — and labeled by its own site name in the `Source` column.

`daily-driver jobs run --list-sources` (or `daily-driver help sources`) prints the live set.

### Commands

```bash
daily-driver jobs run                           # full run, writes jobs.csv
daily-driver jobs run -n                        # dry run (no writes)
daily-driver jobs run --no-enrich               # append only, skip enrichment
daily-driver jobs backfill                      # re-enrich empty cells
daily-driver jobs backfill --limit 20           # cap LLM spend at 20 fit/notes calls
daily-driver jobs run -S remoteok,hn_jobs       # source override
daily-driver jobs promote https://acme.example/jobs/123   # row -> tracker job entry
daily-driver jobs status                        # last-run metadata + csv size
daily-driver jobs prune --older-than month      # archive dropped/rejected/closed rows
```

`jobs run` writes as it works, so an interrupt (Ctrl-C or a scheduled `SIGTERM`) keeps what finished. Each source appends as it completes; LinkedIn and Indeed checkpoint after every search unit. A graceful stop drains in-flight work and exits (`130` for Ctrl-C, `143` for `SIGTERM`); a second Ctrl-C quits immediately. An interrupt during scraping skips enrichment — run `jobs backfill` to fill the empty cells later.

`jobs promote` copies a triaged row into a tracker `job` entry once it needs active driving (interviews, follow-ups). The tracker drives work; `jobs.csv` is the discovery record; promotion is the explicit, idempotent bridge — it never mutates `jobs.csv`. Select by exact Link URL or an unambiguous Company substring.

`jobs prune` requires `--older-than SPEC`. Default target statuses are `dropped`, `rejected`, `closed`; pass `-s active` (repeatable) to override. Pruned rows move to `jobs.archive.csv` — nothing is deleted outright.

### Choosing an AI provider

Enrichment and summary default to the `claude` CLI. Route either to a local Ollama server with the `ai:` and plugin enrichment blocks:

```yaml
ai:
  ollama:
    endpoint: http://localhost:11434
    timeout: 60
plugins:
  job_search:
    enrichment:
      provider: ollama
      model: qwen2.5:14b
```

`claude` suits low-volume, high-quality work; `ollama` suits `jobs backfill` over hundreds of rows (no rate limits, free, local). Interactive launchers always use `claude`. Full walkthrough: [ollama-setup.md](ollama-setup.md). When any task routes to ollama, `daily-driver doctor` adds an `AI providers` row checking server reachability and model presence.

## Summary

Generate a headless summary of any period and copy it to the clipboard.

```bash
daily-driver summary -r today
daily-driver summary -r week --detail high
daily-driver summary -r 2026-05-01:2026-05-09 --match python --match sre
daily-driver summary -r yesterday -j                # raw data, no claude
```

`-r` / `--range` accepts the tracker date grammar plus `YYYY-MM-DD:YYYY-MM-DD`. `--no-clipboard` skips `pbcopy`; `--timeout SECONDS` (default 180) caps the wait for `claude`.

## Voice updates

`voice-profile.md` shapes how `claude` writes on your behalf. Rewrite it from authentic samples (Slack exports, emails, blog posts):

```bash
daily-driver voice-update --from ~/exports/slack.txt --from ~/blog/
daily-driver voice-update --from ~/exports/slack.txt -n        # diff only
daily-driver voice-update --from ~/exports/slack.txt --replace
```

Default mode is `--append`; `--replace` starts over; `-n` / `--dry-run` validates the sources and prints the target path without calling the model or writing (it does not preview the generated profile).

> Run this on writing you actually produced, not AI-generated text — the profile captures your voice, not Claude's.

## Workspace utilities

| Command | Use |
|---|---|
| `paths <kind> [-d DATE] [-j]` | Print a resolved workspace path. Kinds: `root`, `output`, `state`, `ephemeral`, `tracker`, `daily`, `daily-plan`, `daily-notes`, `daily-state`. `-j` emits all paths including the `tracker` key. |
| `gather calendar [--since SPEC] [--until SPEC] [-j]` | Read macOS Calendar (icalBuddy) events. |
| `gather git [--repo PATH] [--since SPEC] [--until SPEC] [-j]` | Recent commits from configured repos (or `--repo PATH`). |
| `status [-j]` | Tracker dashboard. |

## Help

`daily-driver help [TOPIC]` is the in-CLI reference for discoverable values that change with config or release:

| Topic | What it lists |
|---|---|
| `commands` | Every subcommand with a one-line description |
| `statuses` | Recommended statuses + ones in your tracker |
| `categories` | Categories defined in `tracker.categories` |
| `sources` | Job-board sources known to the scraper |
| `dates` | Date-spec grammar |
| `cadences` | Valid `recurring_tasks[].cadence` values |

`daily-driver help -j` emits a structured payload for scripts or completion. No topic prints all sections. This is distinct from argparse `--help`, which only shows flag usage.

## Scheduler (macOS)

`scheduler` manages launchd plists in `~/Library/LaunchAgents/` via three idempotent actions: `install`, `uninstall`, `status`.

Defaults when `scheduler:` is omitted:

| Job | Time | Action |
|---|---|---|
| `checkin` | 11:00, 15:00 | `daily-driver check-in` (skipped while focus is on) |
| `jobs` | 07:00 | `daily-driver jobs run` |
| `day-cycle` | `schedule.day_start` / `schedule.day_end` | Morning / evening launchers |

When launchd fires them, `day-start` and `day-end` open an iTerm2 (or Terminal.app) tab running the session, and `check-in` posts a clickable desktop notification (both via the launchers' `--launch` modes; see [commands.md](commands.md)). The `jobs` scrape runs headless.

Override times in `.dd-config.yaml`. The `scheduler:` block is strictly typed — unknown keys are rejected at config load. Each job takes an optional `days` cadence — `daily` (default), `weekdays`, or a list of day names (e.g. `[sun, wed]`); `scheduler.checkin.days` and `scheduler.jobs.days` scope those jobs, and `schedule.days` applies to both day-start and day-end:

```yaml
schedule:
  day_start: "09:00"
  day_end: "18:00"
  days: weekdays
scheduler:
  checkin: {times: ["10:30", "15:00"], days: weekdays}
  jobs:    {time: "23:59", days: [sun, wed]}
```

Re-run `scheduler install` after changes; it unloads, rewrites, and reloads in one step. Logs land under `.daily-driver/state/logs/launchd-*.{out,err}`.

## Output and verbosity

Daily Driver writes data on stdout and status messages on stderr, so pipelines work without ceremony:

```bash
daily-driver tracker list -s open -j | jq '.[].title'
daily-driver paths daily-plan | xargs $EDITOR
daily-driver summary -r week -j > /tmp/week.json
```

On a TTY, `jobs run` pins a live progress display at the bottom: a `Scraping sources` header bar (green ok / red failed segments) with one bar per source, then an `Enriching jobs` group. Each source reports against its natural unit (search term × country for LinkedIn/Indeed/Apple, boards for Greenhouse, categories for WeWorkRemotely, a single fetch for the rest) and stays pinned at its result (`linkedin  61 found`, red on failure). It ends with a `Completed:` line reconciling totals. Non-interactive output (cron, launchd, pipes), or a terminal that doesn't answer the cursor-position query, falls back to plain lines.

Verbosity is two mutually exclusive flags:

| Flag | Effect |
|---|---|
| (default) | WARNING + ERROR logs; normal status lines; live progress on a TTY |
| `-v` | Adds INFO (per-step progress, per-source scrape lines, enrichment counts), scrolling above the pinned display |
| `-vv` | Adds DEBUG (resolved gather windows, per-job enrichment prompts and AI responses, pre/post field state) |
| `-q`, `--quiet` | Errors only; suppresses status, INFO, DEBUG, and the live display |

Use `-v` for "tell me what just happened" (why a run produced few rows, which calendar window resolved). Use `-vv` to chase a data bug (ollama returning malformed JSON, fields not written). `--no-color` drops ANSI color but keeps Rich markup and table layout — what you want when paging or redirecting output.

## Troubleshooting

Full guide: [troubleshooting.md](troubleshooting.md). Quick map:

| Symptom | Where to look |
|---|---|
| `doctor` errors | Run `doctor --fix`; then [troubleshooting.md#doctor-reports-error](troubleshooting.md#doctor-reports-error) |
| Stale launchd plist or wrong path | `scheduler install` again; else [launchd plist won't load](troubleshooting.md#launchd-plist-wont-load) |
| Focus mode stuck on | `daily-driver focus off`; manual removal in [Focus mode stuck on](troubleshooting.md#focus-mode-stuck-on) |
| Ollama connection refused / model not pulled | [ollama-setup.md#troubleshooting](ollama-setup.md#troubleshooting) |
| Pydantic config error after upgrade | Read `CHANGELOG.md`, edit `.dd-config.yaml`, run `doctor`. No automatic migrations. |

## Reference

- [concepts.md](concepts.md) — mental model and workspace overview
- [commands.md](commands.md) — per-subcommand flag detail
- [configuration.md](configuration.md) — `.dd-config.yaml` schema
- [cli-tree.md](cli-tree.md) — at-a-glance command tree
- [ollama-setup.md](ollama-setup.md) — local-LLM provider
- [troubleshooting.md](troubleshooting.md) — failure modes and recovery
