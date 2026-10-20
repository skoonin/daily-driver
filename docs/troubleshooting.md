# Troubleshooting

> See also: [usage.md](usage.md) for the day-to-day flow.

## Start with `-v` or `-vv`

Most "why did it do that?" questions answer themselves with a more verbose re-run. Verbose output goes to stderr, so it does not corrupt piped data.

In a terminal, `jobs run` pins a live progress display at the bottom ("Scraping sources" and "Enriching jobs" groups, per-source bars that stay pinned at their final result, and a `Completed:` reconciling line). The bars persist at the default level, `-v` and `-vv`; `-q` hides them. `-v` or `-vv` add timestamped logs that scroll above them — that log stream is what you want when debugging. The live display falls back to plain lines automatically when output is piped, run under cron/launchd, or on a terminal that doesn't answer the cursor-position query.

```bash
daily-driver jobs run -v               # INFO: per-source progress, enrichment counts
daily-driver jobs backfill -vv         # DEBUG: per-job prompts, AI responses, write decisions
daily-driver gather calendar -vv       # DEBUG: resolved since/until window
```

Read these lines first when filing a bug or before deeper digging:

- `-v` is enough to see which scraper produced zero rows, how many jobs were filtered by location, how many were eligible for enrichment, and the final `done: X enriched, Y failed, Z skipped` line.
- `-vv` adds the prompt sent to the AI provider, the raw response, the parsed values, and whether each field was actually written (`wrote_fit=true/false`). This is the layer to use when ollama enrichment "succeeds" but the column stays empty.

If a script that consumes Daily Driver output starts misbehaving, run it without `-q` first — quiet mode hides warnings that often explain the issue (e.g. config drift, unknown statuses).

## `doctor` reports ERROR

Run `doctor --fix` first — it regenerates the drifted managed files (`.claude/commands/daily-driver/`, `.claude/agents/daily-driver/`, `.claude/hooks/`, and the workspace `README.md`), restores managed files you may have deleted, and resolves most "missing file" errors. If an error row names a missing Python package (`dep:pydantic`, `dep:pyyaml`, `dep:rich`, `dep:jinja2`), install it and re-run. A missing `claude` command shows as a `cli:claude` WARNING.

```bash
daily-driver doctor
daily-driver doctor --fix       # fixable drift only
daily-driver doctor --reset     # overwrite all managed files, including your edits
```

`doctor` needs a workspace in every mode (exits 1 otherwise).

## Workspace drift (WARNING, not ERROR)

The managed files were generated for a different package version, or for a different set of plugins. A plugin switch reads `plugins changed (none -> job_search)`. Run `daily-driver doctor --fix`. After a plugin switch, also run `scheduler install` if you use the scheduler.

`doctor --fix` keeps your edits to shipped files. It deletes `.md` files you added to `.claude/commands/daily-driver/` or `.claude/agents/daily-driver/`, and `.sh` files you added to `.claude/hooks/`. It also replaces the allow rules saved in `.claude/settings.local.json`. Keep your own commands in `.claude/commands/user/`. See [`.claude/` ownership](configuration.md#claude-ownership) for what `init` and `doctor` keep, overwrite, and delete.

## Config "has settings that daily-driver no longer uses"

Every command except `help` stops with this message when `.dd-config.yaml` contains `user_profile` or `recurring_tasks`. The message names each of these settings and says where its content goes. Delete the block. Move your background into `context.md`, and list recurring tasks under `## Recurring Tasks` in `context.md`. If the file has other mistakes, the message lists them too. Run `daily-driver doctor` to see the message again.

## `jobs` says "job search is off in this workspace"

Job search is opt-in. The message says which step fits your config: add `plugins: {job_search: {}}` to `.dd-config.yaml`, or, if a `job_search` block already sets `enabled: false`, remove that line. Run `daily-driver doctor --fix` to add the job sections to the Claude prompts and README. For a new workspace, use `daily-driver init --job-search`. `jobs run` also needs `scraper.enabled: true` and a source ([usage.md](usage.md#enable-it)); the other `jobs` actions need only job search on.

## Jobs agents still installed after switching job search off

`scheduler status` marks them `yes, job search off`. They keep firing `jobs run`, which exits 1. Run `daily-driver scheduler install` to remove them.

## `Job boards` (WARNING, not ERROR)

An enabled Greenhouse/Ashby/Lever source has no boards pinned in config and an empty (or never-run) `discover-boards` cache, so that platform has nothing to scrape. Run `daily-driver jobs discover-boards` to populate the cache, or pin boards directly under `*_boards` in config.

## `Jobs CSV columns` (OK, not WARNING)

`jobs.csv` carries one or more columns daily-driver's canonical header does not write. Headers are never migrated, so a hand-added column and a column left behind by a rename look identical — this reports informationally rather than as a WARNING, since a column you meant to keep must not raise a row you can never clear. Keep it if it's yours; otherwise delete the column by hand (no automatic migration exists).

## `jobs` fails on Apple (or another Playwright source)

The Apple careers scraper needs Playwright browsers. On macOS, `daily-driver doctor` flags the missing browser when a Playwright source is enabled. Lead with:

```bash
daily-driver doctor --fix
```

For a manual install, see [Playwright browser install](install.md#playwright-browser-install-optional).

Other sources keep working without Playwright. To restrict a run to non-Playwright sources while debugging:

```bash
daily-driver jobs run --sources remoteok,weworkremotely,hn_jobs,hn_who_is_hiring,greenhouse,ashby,lever,workable,workday,linkedin,indeed
```

## launchd plist won't load

`launchctl load` failures usually mean one of the following:

1. **XML malformed.** Run `plutil -lint ~/Library/LaunchAgents/com.daily-driver.<name>.plist`
2. **Label collision** with a previously-loaded plist. Run `launchctl list | grep daily-driver`, unload stragglers with `launchctl unload <path>`, then re-run `daily-driver scheduler install`.
3. **Path to `daily-driver` not absolute.** The scheduler resolves `daily-driver` via `shutil.which` at install time. If you moved your venv after installing, re-run `daily-driver scheduler install`.
4. **macOS blocked the job.** Check System Settings > Login Items and Extensions.

View scheduler logs (one pair per job: `checkin`, `day-start`, `day-end`, and, when job search is on, `jobs` and `jobs-discover`):

```bash
tail -f .daily-driver/state/logs/launchd-checkin.out
tail -f .daily-driver/state/logs/launchd-checkin.err
tail -f .daily-driver/state/logs/launchd-jobs.out
tail -f .daily-driver/state/logs/launchd-jobs.err
tail -f .daily-driver/state/logs/launchd-day-start.out
tail -f .daily-driver/state/logs/launchd-day-end.out
```

## Scheduled day-start / day-end didn't open a session

When launchd fires `day-start` or `day-end`, it cannot open a terminal itself — the launchd background context has no controlling terminal, and from it AppleScript cannot drive iTerm2 / Terminal.app (the Apple Events handshake hangs, notably while the screen is locked). Instead the firing posts a clickable desktop notification; clicking it opens the session in a new tab from your own session, where the one-time Automation permission is available.

- If clicking the notification does nothing, run `daily-driver day-start` (or `day-end`) manually.
- Clickable notifications need `terminal-notifier` (`brew install terminal-notifier`); without it the notification shows the command to run.
- The first click triggers macOS's one-time Automation prompt — grant it under **System Settings > Privacy & Security > Automation** (allow control of iTerm2 / Terminal).
- Check `launchd-day-start.err` / `launchd-day-end.err` if no notification appears at all.

## Tracker locks / "file in use"

A crash does not leave the tracker locked: the operating system releases the lock when the process exits. If a command waits on the tracker, another daily-driver process holds `.daily-driver/state/tracker.lock`. Find it and stop it:

```bash
lsof .daily-driver/state/tracker.lock
```

Do not delete `tracker.yaml`.

## Focus mode stuck on

```bash
daily-driver focus off
```

Or delete the lock manually:

```bash
rm .daily-driver/state/focus.lock
```

## `daily-driver` not on PATH after install

If you installed with `pip install --user`, confirm the user scripts directory is on PATH:

```bash
python3 -m site --user-base
# On macOS, usually ~/Library/Python/3.11 — append /bin to get the scripts dir
```

## Pydantic import error

```text
ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
```

A version mismatch between `pydantic` and `pydantic-core`. Fix it with:

```bash
pip install --force-reinstall pydantic pydantic-core
pip install -e .
```

## Ollama provider issues

If you routed `plugins.job_search.enrichment.provider`, `ai.summary.provider`, or `ai.voice_update.provider` to `ollama`, see [ollama-setup.md](ollama-setup.md#troubleshooting). Common cases: server not running (`connection refused on 11434`), model not pulled (visible in `doctor` as a WARNING row), first request slow (cold load).

## Resetting a workspace

To start clean without losing tracker data:

```bash
cd <workspace> && test -f .dd-config.yaml && rm -rf .claude/commands/daily-driver .claude/agents/daily-driver .claude/hooks .daily-driver && daily-driver init .
```

Replace `<workspace>` with the full path to your workspace. The line stops at the first step that fails, so it deletes nothing outside a workspace. `tracker.yaml` is not touched.

- Kept: your own commands and agents in `.claude/commands/user/`, `.claude/agents/user/`, and the top level of `.claude/commands/` and `.claude/agents/`.
- Lost:
  - every file in the three deleted directories, including your edits to shipped files and the files you added
  - your edits to `README.md`, because the manifest that detects edits is deleted
  - anything you added inside the `permissions`, `hooks`, and `metadata` blocks of `.claude/settings.local.json`, such as saved allow rules
- Cleared: the focus lock, the daily state, the `resume` session pointer, the scheduler logs, and the board discovery cache in `.daily-driver/`. If you use the scheduler, run `daily-driver scheduler install` afterwards.

If `init` fails, fix the error it names and run `daily-driver init .` again. To regenerate only the managed files, run `daily-driver doctor --reset`.
