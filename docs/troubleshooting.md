# Troubleshooting

> See also: [usage.md](usage.md) for the day-to-day flow.

## Start with `-v` or `-vv`

Most "why did it do that?" questions answer themselves with a more verbose re-run. Verbose output goes to stderr, so it does not corrupt piped data.

In a terminal, `jobs run` pins a live progress display at the bottom ("Scraping sources" and "Enriching jobs" groups, per-source bars that stay pinned at their final result, and a `Completed:` reconciling line). The bars persist at every verbosity; `-v` or `-vv` add timestamped logs that scroll above them — that log stream is what you want when debugging. The live display falls back to plain lines automatically when output is piped, run under cron/launchd, or on a terminal that doesn't answer the cursor-position query.

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

Run `doctor --fix` first — it regenerates drifted `.claude/daily-driver/`, restores managed files you may have deleted, and resolves most "missing file" errors. If the error row names a missing dependency (`dep:claude`, `dep:pbcopy`), install it and re-run.

```bash
daily-driver doctor
daily-driver doctor --fix       # fixable drift only
daily-driver doctor --reset     # nuke and regenerate .claude/daily-driver/
```

`--reset` requires a workspace (exits 1 otherwise).

## Workspace drift (WARNING, not ERROR)

The workspace version stamp does not match the installed package version. Safe to ignore for minor bumps; `doctor --fix` picks up new shipped commands and agents.

## `jobs` fails on Apple (or another Playwright source)

The Apple careers scraper needs Playwright browsers. On macOS, `daily-driver doctor` flags the missing browser when a Playwright source is enabled, and `doctor --fix` installs it. Or install directly:

```bash
playwright install firefox
```

Other sources keep working without Playwright. To restrict a run to
non-Playwright sources while debugging:

```bash
daily-driver jobs run --sources remoteok,weworkremotely,hn_jobs,hn_who_is_hiring,greenhouse,ashby,workable,linkedin,indeed
```

## launchd plist won't load

`launchctl load` failures usually mean one of the following:

1. **XML malformed.** Run `plutil -lint ~/Library/LaunchAgents/com.daily-driver.<name>.plist`
2. **Label collision** with a previously-loaded plist. Run `launchctl list | grep daily-driver`, unload stragglers with `launchctl unload <path>`, then re-run `daily-driver scheduler install`.
3. **Path to `daily-driver` not absolute.** The scheduler resolves `daily-driver` via `shutil.which` at install time. If you moved your venv after installing, re-run `daily-driver scheduler install`.
4. **macOS blocked the job.** Check System Settings > Login Items and Extensions.

View scheduler logs:

```bash
tail -f .daily-driver/state/logs/launchd-checkin.out
tail -f .daily-driver/state/logs/launchd-checkin.err
tail -f .daily-driver/state/logs/launchd-jobs.out
tail -f .daily-driver/state/logs/launchd-jobs.err
```

## Tracker locks / "file in use"

Remove a stale flock left after a crash:

```bash
rm .daily-driver/state/tracker.lock
```

Do not delete `tracker.yaml` — the lock file is the transient one.

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

If you routed `plugins.job_search.enrichment.provider` or `ai.summary.provider` to `ollama`, see [ollama-setup.md](ollama-setup.md#troubleshooting). Common cases: server not running (`connection refused on 11434`), model not pulled (visible in `doctor` as a WARNING row), first request slow (cold load).

## Resetting a workspace

To start clean without losing tracker data:

```bash
mv tracker.yaml tracker.yaml.bak
rm -rf .claude .daily-driver
daily-driver init .
mv tracker.yaml.bak tracker.yaml
```
