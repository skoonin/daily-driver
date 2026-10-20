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

Job search is off by default. Add `--job-search` to `init` to switch it on (see [Jobs](#jobs)).

`init` is idempotent — re-running tops up missing files and prints a `Created: ... ; Skipped: ...` summary. It never overwrites `context.md` or `voice-profile.md`. `-f` / `--force` resets `.dd-config.yaml` to a fresh default and rewrites the managed files, so copy any settings you want to keep first. See [`.claude/` ownership](configuration.md#claude-ownership) for what `init` and `doctor` keep, overwrite, and delete.

`doctor` checks contracts and dependencies. WARNINGs exit 0; ERRORs need `doctor --fix` (regenerates managed files, restores deleted ones) or `doctor --reset` (full overwrite).

### 2. Fill in `context.md`

Replace the seeded placeholders with real biographical context — what you do, what you're working on, anything you'd tell a new collaborator. List tasks that repeat under `## Recurring Tasks`, one per line; `day-start` adds each one to your plan. `day-start` and `day-end` read this file, so a richer file means better plans.

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

Spawns a `claude` session with the `work-planner` agent and your workspace attached, then runs `/daily-driver:day-start` — it reads your tracker, recent commits, and calendar, and helps draft the day's plan into `<output_dir>/<YYYY>/<MM>/<YYYY-MM-DD>-plan.md` (`output_dir` is the workspace directory unless you change it; `daily-driver paths daily-plan` prints the path).

### 5. Check in at mid-day

```bash
daily-driver check-in
```

Starts a fresh session by default. Set `claude.resume_check_in: true` to continue your most recent session instead. Update the tracker as you go — via the slash command, or directly with `daily-driver tracker update <ID> -s in-progress`.

### 6. Wind down

```bash
daily-driver day-end
```

Reviews what got done, what didn't, and what to carry forward. Writes the day's notes to `<output_dir>/<YYYY>/<MM>/<YYYY-MM-DD>-notes.md` (`daily-driver paths daily-notes` prints the path). That's the loop — repeat tomorrow.

## Tracker

The load-bearing surface. Categories are free-form: use any name with `-c`, such as `task`, `errand`, `contact`, or `ticket`. List a category under `tracker.categories` in `.dd-config.yaml` to require fields on it and to show it in `daily-driver help categories`.

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
daily-driver tracker add -c errand -T "Book dentist" -s open -d 2026-11-15 --next-action "call the office" --extra phone=555-0100
daily-driver tracker update errand-001 -s blocked -N "waiting on a callback"
daily-driver tracker prune -c errand -s done --older-than month -n   # dry-run first
```

Entry IDs look like `errand-001`: the category, a dash, and a number.

### Statuses

Recommended set: `open`, `in-progress`, `blocked`, `done`, `ruled-out`. Anything else works; the CLI nudges once (to stderr) for a status outside that set that no other entry uses. Silence it with `tracker.warn_unknown_status: false`. Run `daily-driver help statuses` for the current recommended + in-use list. `job` entries (from `jobs promote`) use their own recommended set: `found`, `pending`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, `closed`.

### Date specifiers

`--since` (forward-looking) and `--older-than` (backward-looking) accept:

```
today | yesterday | tomorrow
week | month | quarter | year
Nd | Nw | Nm | Ny           (e.g. 30d, 6w)
YYYY-MM-DD
```

`daily-driver help dates` prints the date tokens and the flags that accept them.

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

If a launcher tab closes, `daily-driver resume` reattaches to the workspace's most recent session.

## Jobs

The job-search plugin scrapes a configurable set of boards, dedupes against `jobs.csv`, and enriches missing fields (fit, notes, comp) via an AI provider.

### Enable it

Job search is off by default, and every `jobs` command exits 1 until it is on. To switch it off again, set `enabled: false` under `plugins.job_search` and run `daily-driver doctor --fix`. If you use the scheduler, run `daily-driver scheduler install` to remove the `jobs` and `jobs-discover` agents. Your settings stay.

1. Switch it on. For a new workspace, run `daily-driver init --job-search <path>`. For an existing one, add a `plugins.job_search` block to `.dd-config.yaml`, then run `daily-driver doctor --fix` to add the job sections to the Claude prompts. `context.md` is not rewritten, so add your location preferences and what you look for in a role by hand. If you use the scheduler, run `daily-driver scheduler install` afterwards to add the `jobs` and `jobs-discover` agents.
2. Set `scraper.enabled: true` and list your sources. If `init --job-search` already wrote a `plugins.job_search` block, merge these settings into it; a second `plugins:` key replaces the first. Minimal:

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
        greenhouse_boards: [company-a, company-b, company-c]
      hn_jobs: {}
      hn_who_is_hiring: {}
      remoteok: {remoteok_tags: [devops, kubernetes, aws]}
      weworkremotely: {wwr_categories: [devops-sysadmin]}
```

Full field reference: [configuration.md](configuration.md#pluginsjob_search).

### Sources

| Source | Notes |
|---|---|
| `remoteok` | RemoteOK feed; `?tags=` slugs from `remoteok_tags` |
| `weworkremotely` | WeWorkRemotely feeds, one per `wwr_categories` entry; scrapes nothing until you list a category |
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
daily-driver jobs discover-boards               # find Greenhouse, Ashby, and Lever boards that list your roles
daily-driver jobs promote https://acme.example/jobs/123   # row -> tracker job entry
daily-driver jobs status                        # last run, row counts by status, board discovery
daily-driver jobs prune --older-than month      # archive triaged rows found over a month ago
daily-driver jobs verify                        # close saved jobs that are no longer posted
```

`jobs run` writes as it works, so an interrupt (Ctrl-C or a scheduled `SIGTERM`) keeps what finished. Each source appends as it completes; LinkedIn and Indeed checkpoint after every search unit. A graceful stop drains in-flight work and exits (`130` for Ctrl-C, `143` for `SIGTERM`); a second Ctrl-C quits immediately. An interrupt during scraping skips enrichment — run `jobs backfill` to fill the empty cells later.

`jobs promote` copies a triaged row into a tracker `job` entry once it needs active driving (interviews, follow-ups). The tracker drives work; `jobs.csv` is the discovery record; promotion is the explicit, idempotent bridge — it never mutates `jobs.csv`. Select by exact Link URL or an unambiguous Company substring.

`jobs prune --older-than SPEC` defaults to `14d`. Default target statuses are `dropped`, `rejected`, `closed`, `skipped`; pass `-s STATUS` (repeatable) to override, for example `-s applied -s interviewing`. Those statuses age from `Date Found`, while `--min-fit` rows age from `Date Verified` — see `commands.md` for why. Pruned rows move to `jobs.archive.csv` — nothing is deleted outright.

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

`claude` suits low-volume, high-quality work; `ollama` suits `jobs backfill` over hundreds of rows (no rate limits, free, local). Interactive launchers always use `claude`. Full walkthrough: [ollama-setup.md](ollama-setup.md). When a task routes to ollama, `daily-driver doctor` checks server reachability and model presence: an `AI providers` row for `summary` and `voice-update`, and an `Enrichment provider` row for job search enrichment.

## Summary

Generate a headless summary of any period and copy it to the clipboard.

```bash
daily-driver summary -r today
daily-driver summary -r week --detail high
daily-driver summary -r 2026-05-01:2026-05-09 --match python --match sre
daily-driver summary -r yesterday -j                # resolved date range as JSON, no claude call
```

`-r` / `--range` accepts the tracker date grammar plus `YYYY-MM-DD:YYYY-MM-DD`. `--no-clipboard` skips `pbcopy`; `--timeout SECONDS` (default 180) caps the wait for `claude`.

## Voice updates

`voice-profile.md` shapes how `claude` writes on your behalf. Rewrite it from authentic samples (Slack exports, emails, blog posts):

```bash
daily-driver voice-update --from ~/exports/slack.txt ~/blog/
daily-driver voice-update --from ~/exports/slack.txt -n        # check the sources, no model call, no write
daily-driver voice-update --from ~/exports/slack.txt --replace
```

`--from` takes one or more files or directories after a single flag; repeating `--from` keeps only the last one. Only `.md` and `.txt` files up to 500,000 bytes are read.

Default mode is `--append`; `--replace` starts over; `-n` / `--dry-run` validates the sources and prints the target path without calling the model or writing (it does not preview the generated profile).

> Run this on writing you actually produced, not AI-generated text — the profile captures your voice, not Claude's.

## Workspace utilities

| Command | Use |
|---|---|
| `paths <kind> [-d DATE] [-j]` | Print a resolved workspace path. Kinds: `root`, `output`, `state`, `ephemeral`, `tracker`, `daily`, `daily-plan`, `daily-notes`, `daily-state`. `-j` emits all paths including the `tracker` key. |
| `gather calendar [--since SPEC] [--until SPEC] [-j]` | Read macOS Calendar (icalBuddy) events. |
| `gather git [--repo PATH] [--since SPEC] [--until SPEC] [-j]` | Recent commits from configured repos (or `--repo PATH`). |
| `status [-j]` | Tracker dashboard. |
| `calendar sync` | Write today's plan time blocks to a macOS Calendar. Off until `calendar.sync_enabled: true`; the calendar named in `calendar.plan_calendar_name` (default `Daily Plan`) must already exist. `day-start` runs it for you. |

## Help

`daily-driver help [TOPIC]` is the in-CLI reference for discoverable values that change with config or release:

| Topic | What it lists |
|---|---|
| `commands` | Every subcommand with a one-line description |
| `statuses` | Recommended statuses + ones in your tracker |
| `categories` | Categories defined in `tracker.categories` |
| `sources` | Job-board sources known to the scraper |
| `dates` | Date-spec grammar |

`daily-driver help -j` emits a structured payload for scripts or completion. No topic prints all sections. This is distinct from argparse `--help`, which only shows flag usage.

## Scheduler (macOS)

`scheduler` manages launchd plists in `~/Library/LaunchAgents/` via three idempotent actions: `install`, `uninstall`, `status`.

Defaults when `scheduler:` is omitted:

| Job | Time | Action |
|---|---|---|
| `checkin` | 11:00, 15:00 | `daily-driver check-in` (skipped while focus is on) |
| `jobs` | 07:00 (job search only) | `daily-driver jobs run` |
| `jobs-discover` | 23:59 Sat, Tue (job search only) | `daily-driver jobs discover-boards` |
| `day-start` | `schedule.day_start` (off until set) | `daily-driver day-start` |
| `day-end` | `schedule.day_end` (off until set) | `daily-driver day-end` |

When launchd fires them, `day-start`, `day-end`, and `check-in` each post a desktop notification (clickable when `terminal-notifier` is installed; otherwise it shows the command to run). Clicking opens the session in a new iTerm2 tab, or in Terminal.app if iTerm2 is not installed (via the launchers' `--launch notify` mode; see [commands.md](commands.md)). launchd cannot open a terminal itself, so the notification is the nudge. The `jobs` and `jobs-discover` runs are headless.

Override times in `.dd-config.yaml`. The `scheduler:` block is strictly typed — unknown keys are rejected at config load. Each job takes an optional `days` cadence — `daily` (default), `weekdays`, or a list of day names (e.g. `[sun, wed]`); `scheduler.checkin.days`, `scheduler.jobs.days`, and `scheduler.discovery.days` scope those jobs, and `schedule.days` applies to both day-start and day-end:

```yaml
schedule:
  day_start: "09:00"
  day_end: "18:00"
  days: weekdays
scheduler:
  checkin: {times: ["10:30", "15:00"], days: weekdays}
  jobs:    {time: "23:59", days: [sun, wed]}
```

Re-run `scheduler install` after changes; it unloads, rewrites, and reloads in one step. With job search off, a full `scheduler install` (no job names) also removes any `jobs` and `jobs-discover` agents left from an earlier install and lists them; `scheduler status` marks such agents `yes, job search off`. Logs land under `.daily-driver/state/logs/launchd-*.{out,err}`.

## Output and verbosity

Daily Driver writes data on stdout and status messages on stderr, so pipelines work without ceremony:

```bash
daily-driver tracker list -s open -j | jq -r '.data.entries[].title'
daily-driver paths daily-plan | xargs $EDITOR
daily-driver summary -r week -j > /tmp/week.json
```

`-j` output is wrapped as `{"schema": 1, "data": ...}`, so read fields under `.data`.

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
