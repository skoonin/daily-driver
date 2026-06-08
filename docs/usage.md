# Using Daily Driver

End-to-end guide to the daily flow plus the surfaces you'll touch most
often: tracker, focus, jobs, day-cycle launchers, summary, voice, and
scheduler. For the mental model and workspace layout, start with
[concepts.md](concepts.md). For installation see [install.md](install.md).
For configuration syntax see [configuration.md](configuration.md). For
exhaustive per-flag detail see [commands.md](commands.md).

## First day

A walkthrough you can run end-to-end the first time you set up.

### 1. Scaffold the workspace

```bash
daily-driver init ~/daily-driver-workspace
cd ~/daily-driver-workspace
daily-driver doctor
```

`init` is idempotent. Re-running it tops up missing files and prints a
`Created: ... ; Skipped: ...` summary. It will not overwrite `context.md`
or `voice-profile.md` once they exist; pass `-f` / `--force` only if you
want to reset `.dd-config.yaml` (the previous copy is preserved as
`.dd-config.yaml.bak`).

`doctor` checks contracts and dependencies. Exit 0 with WARNINGs is fine;
ERRORs need `doctor --fix` (regenerates managed files and restores any you
accidentally deleted) or `doctor --reset` (nuclear overwrite).

### 2. Fill in `context.md`

Open `context.md` in your editor and replace the seeded placeholders with
real biographical context: name, timezone, what you do, what you're
working on, anything you would tell a new collaborator on day one. This is
the file `claude` reads before every session, so the better it is, the
better the conversations.

`voice-profile.md` can wait until you have writing samples — see
[Voice updates](#voice-updates) below.

### 3. Add a first tracker entry

```bash
daily-driver tracker add -c task -T "draft Q2 OKRs" -s open
daily-driver tracker list
daily-driver status
```

`status` is the dashboard. It surfaces setup gaps first (if any), totals
by category and status, stalled items, and seven days of activity.

### 4. Start the day

```bash
daily-driver day-start
```

Spawns a `claude` session with the `work-planner` agent and your workspace
attached. It runs `/day-start`, which reads your tracker, recent commits,
and calendar events, and helps you draft the day's plan. The plan is
written to `<workspace>/<YYYY>/<MM>/<DD>/plan.md`.

### 5. Check in at mid-day

```bash
daily-driver check-in
```

By default this resumes the morning session (set
`claude.resume_check_in: false` in `.dd-config.yaml` to start fresh).
Update the tracker as you go — `claude` can do this via the slash command,
or you can run `daily-driver tracker update <ID> -s in-progress` directly.

### 6. Wind down

```bash
daily-driver day-end
```

Reviews what got done, what didn't, and what to carry forward. Appends to
the day's `notes.md`.

That's the loop. Repeat tomorrow.

## Tracker

The tracker is the load-bearing surface. Categories are config-driven —
edit `.dd-config.yaml` under `tracker.categories` to add `job`, `errand`,
`contact`, `ticket`, or anything else.

### Lifecycle commands

| Command | Purpose |
|---|---|
| `tracker add -c CAT -T TEXT [...]` | Create a new entry |
| `tracker list [-c CAT] [-s STATUS] [-t TAG] [--since SPEC] [-j]` | Filter / print as table or JSON |
| `tracker show ID [-j]` | Single-entry detail view |
| `tracker update ID [-s STATUS] [-N NOTE] [-t TAGS] [...]` | Update; `-N`/`--note` appends; `-t`/`--tags` replaces |
| `tracker delete ID` | Remove one entry |
| `tracker prune [-c CAT] [-s STATUS] [--older-than SPEC] [-n]` | Bulk delete (at least one filter required) |
| `tracker follow-ups [--overdue] [-j]` | Entries with `next_action` set |
| `tracker stats [-j]` | Counts by category and status |

### Common examples

```bash
# Add a job application with extras
daily-driver tracker add -c job -T "Senior SRE at Acme" \
    -s open -l https://acme.example/jobs/123 \
    --extra company=Acme --extra source=linkedin

# Update status and append a note
daily-driver tracker update abc123 -s blocked -N "waiting on referral"

# Find things that are stalled
daily-driver tracker list -s open --since week

# Prune dropped jobs older than a month (dry-run first)
daily-driver tracker prune -c job -s dropped --older-than month -n
daily-driver tracker prune -c job -s dropped --older-than month
```

### Statuses

Recommended set: `open`, `in-progress`, `blocked`, `done`, `ruled-out`.
You can use anything else; the CLI just nudges once (to stderr) when you
introduce a status outside that set and that no other entry uses. To
silence the nudge globally, set `tracker.warn_unknown_status: false` in
`.dd-config.yaml`.

Run `daily-driver help statuses` for the current recommended + in-use
list.

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

A flock-backed toggle that other Daily Driver commands respect — most
notably, scheduled check-ins skip while focus is on.

```bash
daily-driver focus on --for 90m --reason "deep work on Q2 OKRs"
daily-driver focus status
daily-driver focus off
```

`--for` accepts `30m`, `2h`, `1h30m`, or bare minutes. Omitting it falls
back to `focus.default_duration` in `.dd-config.yaml` (default `25m`).
`focus status -j` emits JSON.

The lock lives at `.daily-driver/state/focus.lock`. If it gets stuck,
`focus off` releases it; in the rare case the process died holding the
lock, delete the file directly.

## Day cycle launchers

Three thin wrappers around `claude` that spawn a workspace-aware session
with the `work-planner` agent.

| Command | Slash command run | Typical time |
|---|---|---|
| `day-start` | `/day-start` | morning |
| `check-in` | `/check-in` | midday |
| `day-end` | `/day-end` | evening |

Shared flags: `--session-name NAME`, `--agent NAME` (default
`work-planner`), `--model {sonnet,opus,haiku}`.

`check-in` adds `--no-resume` to force a fresh session instead of resuming
the morning's. The default is governed by `claude.resume_check_in` in
`.dd-config.yaml`.

### In-session slash commands

These ship to `.claude/commands/daily-driver/` but are not CLI subcommands —
invoke them from inside a running `claude` session.

| Slash command | What it does |
|---|---|
| `/daily-learning` | 15-30 minute learning drill (behavioral STAR, technical fundamentals, system design, etc.). Rotates topics by day of week, avoids recent repeats, appends to `<output>/interview-practice/<date>.md`. Offered as an opt-in step inside `/day-start` and can be run standalone. |

## Jobs

The job-search plugin scrapes a configurable set of boards, dedupes against
existing rows in `jobs.csv`, and uses an AI provider to enrich missing
fields (fit, notes, comp, Glassdoor rating).

### Enable it

Add a `plugins.job_search` block to `.dd-config.yaml` and set
`scraper.enabled: true`. Minimal example:

```yaml
plugins:
  job_search:
    persona: "Senior SRE, IC-track only"
    seniority_keywords: [senior, staff, principal]
    locations:
      home_city: Vancouver, BC
      remote: true
      countries: [US, CA]
    scraper:
      enabled: true
    sources:
      greenhouse:
        enabled: true
        greenhouse_boards: [anthropic, stripe, figma]
      hn_jobs: {}
      hn_who_is_hiring: {}
      remoteok: {}
      weworkremotely: {}
```

Full field reference in [configuration.md](configuration.md#pluginsjob_search).

### Sources

| Source | Notes |
|---|---|
| `remoteok` | RemoteOK feed |
| `weworkremotely` | WeWorkRemotely (category-aware) |
| `hn_who_is_hiring` | Hacker News monthly thread |
| `hn_jobs` | YC-funded jobs |
| `greenhouse` | One row per `greenhouse_boards` entry |
| `linkedin` | LinkedIn via JobSpy |
| `indeed` | Indeed via JobSpy |
| `apple` | Apple careers (requires Playwright) |

`linkedin` and `indeed` are the CLI selectors for `jobs run -S`.
In `.dd-config.yaml` they are sub-toggles under the `jobspy:` source
(`jobspy.linkedin`, etc.), not top-level source keys.

`daily-driver jobs run --list-sources` prints the live set. `daily-driver
help sources` does the same.

### Commands

```bash
daily-driver jobs run                           # full run, writes jobs.csv
daily-driver jobs run -n                        # dry run (no writes)
daily-driver jobs run --backfill                # re-enrich empty cells
daily-driver jobs run -S remoteok,hn_jobs       # source override

daily-driver jobs status                        # last-run metadata + csv size
daily-driver jobs prune --older-than month      # archive dropped/rejected/closed rows
```

`jobs prune` requires `--older-than SPEC`. Default target statuses are
`dropped`, `rejected`, `closed`; pass `-s active` (repeatable) to override.
Pruned rows move to `jobs.archive.csv` — nothing is deleted outright.

### Choosing an AI provider

Enrichment and summary tasks default to the `claude` CLI. You can route
either (or both) to a local Ollama server with the `ai:` config block:

```yaml
ai:
  enrichment:
    provider: ollama
    model: qwen2.5:14b
  # summary stays on claude by default
  ollama:
    endpoint: http://localhost:11434
    timeout: 60
```

`claude` is the right pick for low-volume / high-quality tasks. `ollama`
is the right pick for `jobs run --backfill` over hundreds of rows: no
rate limits, free, fully local. Interactive launchers always use `claude`.
Full setup walkthrough: [ollama-setup.md](ollama-setup.md).

When at least one task is routed to ollama, `daily-driver doctor` adds an
`AI providers` row that checks server reachability and confirms the model
is pulled.

## Summary

Generate a headless summary of any period and pipe it to the clipboard.

```bash
daily-driver summary -r today
daily-driver summary -r week --detail high
daily-driver summary -r 2026-05-01:2026-05-09 --match python --match sre
daily-driver summary -r yesterday -j                # raw data, no claude
```

`-r` / `--range` accepts the same grammar as tracker dates plus
`YYYY-MM-DD:YYYY-MM-DD`. `--no-clipboard` skips the `pbcopy` step.
`--timeout SECONDS` (default 180) controls how long to wait for `claude`.

## Voice updates

`voice-profile.md` shapes how `claude` writes on your behalf. To rewrite
it from authentic samples (Slack exports, emails, blog posts, anything
text-based):

```bash
daily-driver voice-update --from ~/exports/slack.txt --from ~/blog/
daily-driver voice-update --from ~/exports/slack.txt -n   # diff only
daily-driver voice-update --from ~/exports/slack.txt --replace
```

Default mode is `--append`; pass `--replace` to start over. `-n` /
`--dry-run` prints the would-be diff without writing.

> Run this on writing you actually produced, not on AI-generated text — the
> profile is supposed to capture your voice, not Claude's.

## Workspace utilities

| Command | Use |
|---|---|
| `paths <kind> [-d DATE] [-j]` | Print a resolved workspace path. Kinds: `root`, `output`, `state`, `ephemeral`, `daily`, `daily-plan`, `daily-notes`, `daily-state`. Useful for shell pipelines. |
| `gather calendar [--since SPEC] [--until SPEC] [-j]` | Read macOS Calendar (icalBuddy) events as JSON. |
| `gather git [--repo PATH] [--since SPEC] [--until SPEC] [-j]` | Recent commits from the configured repos (or `--repo PATH`). |
| `status [-j]` | Tracker dashboard. |

## Help

`daily-driver help [TOPIC]` is the in-CLI reference for discoverable
values that change with config or release:

| Topic | What it lists |
|---|---|
| `commands` | Every subcommand with one-line description |
| `statuses` | Recommended statuses + ones already in your tracker |
| `categories` | Categories defined in your `tracker.categories` |
| `sources` | Job-board sources known to the scraper |
| `dates` | Date-spec grammar |
| `cadences` | Valid `recurring_tasks[].cadence` values |

`daily-driver help -j` emits a structured payload for scripts or shell
completion. `daily-driver help` (no topic) prints all sections.

This is distinct from argparse `--help`, which only shows flag usage.

## Scheduler (macOS)

`scheduler` manages launchd plists in `~/Library/LaunchAgents/`. Three
sub-actions: `install`, `uninstall`, `status`. All idempotent.

Defaults when `scheduler:` is omitted from `.dd-config.yaml`:

| Job | Time | Action |
|---|---|---|
| `checkin` | 11:00, 15:00 | `daily-driver check-in` (skipped while focus is on) |
| `jobs` | 07:00 | `daily-driver jobs run` |
| `day-cycle` | `schedule.day_start` / `schedule.day_end` | Morning / evening launchers |

Override times in `.dd-config.yaml`:

```yaml
scheduler:
  checkin: {times: ["10:30", "15:00"]}
  jobs:    {time: "06:30"}
```

The block is freeform — keys are passed through to the Jinja launchd
templates. Re-run `daily-driver scheduler install` after changes; it
unloads, rewrites, and reloads in one step.

Logs land under `.daily-driver/state/logs/launchd-*.{out,err}`.

## Output and verbosity

Daily Driver writes data on stdout and status messages on stderr. Tables, JSON payloads, and resolved paths go to stdout; informational lines, warnings, and log output go to stderr. That split is what lets these pipelines work without ceremony:

```bash
daily-driver tracker list -s open -j | jq '.[].title'
daily-driver paths daily-plan | xargs $EDITOR
daily-driver summary -r week -j > /tmp/week.json
```

The status banners ("Starting jobs run...", "Scrape complete: ...") still print to your terminal because they are on stderr — they do not contaminate the piped data.

On a TTY, `jobs run` pins a live progress display at the bottom of the terminal. Every source is its own progress bar that fills as it works and then stays pinned at its result (`linkedin  61 found`, recoloured red on failure); a `Scraping sources` header bar tracks overall completion with green (ok) and red (failed) segments. Each source reports progress against its own natural unit — search term × country for LinkedIn/Indeed/Apple, boards for Greenhouse, categories for WeWorkRemotely, a single fetch for the rest — so even a fast source shows a bar. An `Enriching jobs` group then shows detail / company / fit fill bars. Known-slow boards show a one-time "running -- can take several minutes" note until real progress arrives, so a long quiet stretch doesn't look like a hang. It ends with a `Completed:` line that reconciles totals (found -> new -> matched location, plus "(N skipped by location)" when applicable). The bars persist at every verbosity; verbosity controls only how much log volume scrolls above them. Problems surface live above the bars as they happen, with a terse `Warnings: N (shown above)` line at the end. A non-interactive stream (cron, launchd, pipes) — or a terminal that doesn't answer the cursor-position query on entry — falls back to plain line-per-row output with no ANSI.

Verbosity is controlled by two mutually exclusive flags:

| Flag | Effect |
|---|---|
| (default) | WARNING and ERROR logs; normal status lines; live progress display on a TTY for `jobs run` |
| `-v` | Adds INFO logs (per-step progress, source-by-source scrape lines, enrichment startup/end counts), timestamped and scrolling above the persistent `jobs run` display |
| `-vv` | Adds DEBUG logs (resolved gather windows, per-job enrichment prompts and AI responses, pre/post field state) |
| `-q`, `--quiet` | Errors only; suppresses status, INFO, DEBUG, and warnings-as-status |

Use `-v` for "tell me what just happened" — e.g. why a `jobs run` produced few rows, or which calendar window `gather calendar` resolved. Use `-vv` when chasing a specific data bug: ollama returning malformed JSON, enrichment writing fewer fields than expected, or a tracker entry that vanished from a filter.

`--no-color` disables ANSI color but keeps Rich markup and table layout intact, which is what you want when piping output through a pager or redirecting to a file that another tool will read.

## Troubleshooting

The full guide is [troubleshooting.md](troubleshooting.md). Quick map:

| Symptom | Where to look |
|---|---|
| `doctor` errors | Run `doctor --fix`; then [troubleshooting.md#doctor-reports-error](troubleshooting.md#doctor-reports-error) |
| Stale launchd plist or wrong path | `scheduler install` again; if it still fails, [launchd plist won't load](troubleshooting.md#launchd-plist-wont-load) |
| Focus mode stuck on | `daily-driver focus off`; manual lock removal documented in [Focus mode stuck on](troubleshooting.md#focus-mode-stuck-on) |
| Ollama connection refused / model not pulled | [ollama-setup.md#troubleshooting](ollama-setup.md#troubleshooting) |
| Pydantic config error after upgrade | Read `CHANGELOG.md`, edit `.dd-config.yaml`, run `doctor`. No automatic migrations. |

## Reference

- [concepts.md](concepts.md) — mental model and workspace overview
- [commands.md](commands.md) — per-subcommand flag detail
- [configuration.md](configuration.md) — `.dd-config.yaml` schema and customization
- [cli-tree.md](cli-tree.md) — at-a-glance command tree
- [ollama-setup.md](ollama-setup.md) — local-LLM provider
- [troubleshooting.md](troubleshooting.md) — failure modes and recovery
