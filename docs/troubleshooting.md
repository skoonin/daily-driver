# Troubleshooting

> See also: [usage.md](usage.md) for the day-to-day flow.

## `doctor` reports ERROR

Run `doctor --fix` first — it regenerates drifted `.claude/daily-driver/`,
restores managed files you may have deleted, and resolves most "missing file"
errors. If the error row mentions a missing dependency (`dep:claude`,
`dep:pbcopy`), install it and re-run.

```bash
daily-driver doctor
daily-driver doctor --fix       # fixable drift only
daily-driver doctor --reset     # nuke and regenerate .claude/daily-driver/
```

`--reset` requires a workspace (exits 1 otherwise).

## Workspace drift (WARNING, not ERROR)

The workspace version stamp does not match the installed package version. Safe
to ignore for minor version bumps; `doctor --fix` picks up new shipped commands
and agents.

## `jobs` fails on Apple (or another Playwright source)

The Apple careers scraper (and any user-added source with `type: playwright`)
needs Playwright browsers. Install them:

```bash
pip install playwright
playwright install chromium
```

Other sources keep working without Playwright. To restrict a run to
non-Playwright sources while debugging:

```bash
daily-driver jobs run --sources remoteok,weworkremotely,hn_jobs,hn_who_is_hiring,greenhouse,jobspy
```

## launchd plist won't load

`launchctl load` failures usually mean one of the following:

1. **XML malformed.** Run `plutil -lint ~/Library/LaunchAgents/com.daily-driver.<name>.plist`
2. **Label collision** with a previously-loaded plist. Run
   `launchctl list | grep daily-driver`, unload any stragglers with
   `launchctl unload <path>`, then re-run `daily-driver scheduler install`.
3. **Path to `daily-driver` not absolute.** The scheduler resolves `daily-driver`
   via `shutil.which` at install time. If you moved your venv after installing,
   re-run `daily-driver scheduler install`.
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

If you installed with `pip install --user`, confirm the user scripts directory
is on PATH:

```bash
python3 -m site --user-base
# On macOS, usually ~/Library/Python/3.11 — append /bin to get the scripts dir
```

## Pydantic import error

```
ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
```

This is caused by a version mismatch between `pydantic` and `pydantic-core`.
Fix it with:

```bash
pip install --force-reinstall pydantic pydantic-core
pip install -e .
```

## Ollama provider issues

If you routed `ai.enrichment.provider` or `ai.summary.provider` to `ollama`,
see [ollama-setup.md](ollama-setup.md#troubleshooting). Common cases:
server not running (`connection refused on 11434`), model not pulled
(visible in `doctor` as a WARNING row), first request slow (cold load).

## Resetting a workspace

To start clean without losing tracker data:

```bash
mv tracker.yaml tracker.yaml.bak
rm -rf .claude .daily-driver
daily-driver init .
mv tracker.yaml.bak tracker.yaml
```
