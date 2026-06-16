# Developer overview

Daily Driver is a pure-Python CLI. No compiled extensions, no non-Python assets beyond Jinja templates and packaged markdown. The package lives under `source/daily_driver/`.

## Product split

The program owns the durable record (tracker entries, daily notes, job leads, focus locks, config — all plain YAML/CSV on disk). Claude owns the conversation. Claude does not retain information between sessions; the program does. Every piece of user-visible state is a file you can `cat`, diff, and commit, regardless of whether Claude was reachable when it was written.

## Module map

```
daily_driver/
├── __init__.py              # __version__ (single source of truth)
├── __main__.py              # python -m daily_driver entry
├── cli/
│   ├── cli.py               # static _COMMANDS table + dispatch loop
│   ├── _common.py           # global-flag registration + post-parse configure()
│   └── commands/            # one module per subcommand; argparse + presentation only
├── core/                    # workspace, config, tracker, locking, generate, scheduler...
│   ├── console.py           # two-stream Rich Console (stdout + stderr)
│   ├── logging.py           # stdlib logger with plain counting StreamHandler on stderr
│   ├── progress.py          # domain-neutral live progress display (enlighten)
│   └── (no subprocess calls, no TTY/clock/network deps — pure unit-testable)
├── gathers/                 # read-only readers: calendar, git
├── integrations/            # subprocess + external-service boundary: claude_cli,
│                            #   ai_provider, ollama_client, clipboard, launchd,
│                            #   git, icalbuddy, notify
├── plugins/                 # feature slices wired through a static registry
│   ├── __init__.py          # PLUGINS tuple + _validate_registry (import-time check)
│   ├── _base.py             # Plugin / PackageDataDir frozen dataclasses (the contract)
│   ├── config.py            # PluginsConfig assembled from PLUGINS (extra="forbid")
│   └── job_search/          # the one shipped plugin
│       ├── cli.py           # jobs subcommand (add_parser / run)
│       ├── config.py        # JobSearchPlugin pydantic model (plugins.job_search)
│       ├── scheduler.py     # build_scheduled_jobs(ctx)
│       ├── doctor.py        # run_checks(workspace)
│       ├── jobs_archive.py  # prune stale jobs.csv rows
│       ├── jobs_lock.py     # flock guard for jobs.csv
│       ├── scraper_status.py  # last-run metadata
│       ├── templates/       # PACKAGE DATA — jobs.plist.j2 (Jinja)
│       └── scraper/         # comp, parsing, csv_io, enrichment, models, runner
│           ├── enrichment/   # detail-page + LLM enrichers (EnrichedJob in/out)
│           └── sources/     # one module per source + _http.py shared helpers
└── resources/               # PACKAGE DATA — bundled into the wheel
    ├── slash_commands/daily-driver/  # shipped slash commands
    ├── agents/daily-driver/          # shipped agents
    ├── launchd/             # core plist Jinja templates (checkin.plist.j2)
    └── templates/           # scaffold + settings Jinja templates + hooks/
```

### Layer rules

- **`cli/`** — argparse + Rich only. `add_parser(subparsers, parents)` + `run(args) -> int`. Non-trivial logic belongs in `core/<same-name>.py`. Use `core.console.Console` for user-facing status / errors; reserve bare `print()` for machine-readable stdout payloads (JSON, single resolved paths).
- **`core/`** — business logic. No subprocess, no TTY, no network. Unit-testable in isolation.
- **`gathers/`** — typed readers returning plain dataclasses. No YAML, no printing.
- **`plugins/`** — feature slices wired through the static `PLUGINS` registry (no runtime discovery). Each plugin owns its CLI, config, and optional scheduler / doctor / package-data under `plugins/<name>/`. `job_search` is the reference plugin; its scraper lives at `plugins/job_search/scraper/` — focused modules `comp.py`, `parsing.py`, `csv_io.py`, `enrichment/` (detail-page + LLM enrichers), `models.py`, `runner.py` (orchestration + filters + dedup), and `scraper/sources/<name>.py` (one file per source). `sources/__init__.py` registers each source in the `SCRAPERS` dict. Past the source-adapter boundary every job is one frozen `EnrichedJob`: adapters emit wire-format dicts, `runner._enriched_from_scraped` lifts each to an `EnrichedJob` once, and enrichers / CSV read-write / backfill all operate on that single representation through one `CANONICAL_HEADER` (derived from `EnrichedJob.CSV_COLUMN_TO_ATTR`). See "Plugins" below and [extending.md](extending.md).
- **`integrations/`** — the single subprocess + external-service boundary. Every `claude`, `pbcopy`, `launchctl`, `git`, `icalBuddy`, and desktop-notification call goes through here, alongside the `ai_provider` / `ollama_client` adapters. Wrappers raise domain exceptions (e.g. `claude_cli` raises `ClaudeNotFoundError` / `ClaudeInvocationError` / `ClaudeTimeoutError`; `git` raises `GitCommandError`) so callers handle typed failures instead of inspecting return codes. Tests monkeypatch one module instead of many.
- **`resources/`** — package data only, bundled into the wheel and accessed via `importlib.resources`: `slash_commands/daily-driver/` and `agents/daily-driver/` (shipped `.md`, copied into the workspace `.claude/` tree), `launchd/` (core plist templates), and `templates/` (scaffold + settings Jinja, plus `hooks/`). No importable logic.

## Runtime flow

`daily-driver` (console entry) → `daily_driver.cli.cli.app()`:

1. Argparse tree built from static `_COMMANDS` table. Subcommand modules are imported lazily (`importlib.import_module`) — startup stays cheap.
2. Global flags (`--workspace`, `--verbose`/`-v`/`-vv`, `--quiet`, `--no-color`) are registered via `cli/_common.py:add_global_flags(parser)` on both the top-level parser and each leaf, AFTER local args, so they render under a "global options" group at the bottom of `--help`. All globals use `default=argparse.SUPPRESS`; reads must use `getattr(args, name, default)`.
3. `cli/_common.py:configure(args)` runs once after parse: it calls `core.logging.configure(verbosity)` (mapping `-q`/`-v`/`-vv` to `ERROR`/`INFO`/`DEBUG`; bare invocation is `WARNING`), then `Console.setup_for_user(quiet, verbose, no_color)`. Both helpers are idempotent — second calls remove the existing counting handler before re-attaching.
4. Most subcommands call `Workspace.discover_or_fail(override=args.workspace)`. Precedence: `--workspace` flag, upward search for `.dd-config.yaml`, fail with `WorkspaceError`.
5. `core.config.load()` parses YAML (`safe_load`) and validates against `core.config_models.Config`. Pydantic v2, `extra="forbid"` at every nesting level. Invalid config fails at parse, not mid-command.
6. `core.version_stamp` compares the workspace's stamped version against `daily_driver.__version__`. On mismatch: `doctor --fix` regenerates; other subcommands proceed without touching the filesystem.
7. Subcommand `run(args)` does the work and returns an int exit code. Errors are caught at the top of `app()`: at `-v` or higher the daily_driver logger emits a full `logger.exception()` traceback to stderr; otherwise `Console.error(str(exc))` prints a single-line message. Non-zero exit either way — no tracebacks on happy-path failures.

Scheduled invocations go through the same entry point. `scheduler install` renders a `<job>.plist.j2` Jinja template with workspace-specific paths and calls `launchctl load`. Core jobs render from `resources/launchd/` (e.g. `checkin.plist.j2`); a plugin ships its own template alongside its code (`job_search` renders `plugins/job_search/templates/jobs.plist.j2`). stdout/stderr redirect to `.daily-driver/state/logs/launchd-<job>.{out,err}` via the plist.

## Generate lifecycle

`generate(workspace, force=False)` writes the full package-managed set into the workspace:

- `.claude/commands/daily-driver/*.md` and `.claude/agents/daily-driver/*.md` from `resources/slash_commands/` and `resources/agents/` (manifest-guarded)
- `.claude/settings.local.json`, rendered from the template and merged (not manifest-guarded — see below)
- the rendered contract root templates (`ENTRIES`, e.g. `README.md`) (manifest-guarded)
- `.claude/hooks/*.sh` from `resources/templates/hooks/` (manifest-guarded)

Those are the only workspace paths generate touches; all other user files are sacred. Each `PackageDataDir` declares its source package and `.claude/` destination; `generate` walks core's baseline dirs plus every registered plugin's `package_data_dirs`.

Key invariants:

- **Version stamp written last.** A crash mid-generate leaves the stamp stale; the next invocation re-runs. Idempotent by construction.
- **SHA-256 manifest guards user edits.** `_copy_package_md` (the `.md` files), `_render_contract_entries` (root templates), and `_copy_hooks` (the `.sh` hooks) all check the manifest before overwriting and record each written file's hash. A user edit flips the hash; subsequent generates skip that file and log a warning. `doctor --fix` respects this; `doctor --reset` (`force_overwrite=True`) is the nuclear option that overwrites. `settings.local.json` is the exception — it merges rather than being manifest-guarded.
- **`settings.local.json` merges instead of overwriting.** Package-managed top-level keys are refreshed from the template; user-added top-level keys are preserved.
- **Concurrency.** Generate acquires an exclusive `generate.lock` under `ephemeral_dir` and re-checks drift inside the lock (double-checked locking).

## Init contract

`core/contract.py` codifies every artifact `daily-driver init` promises to produce. `doctor` runs `contract.check()` and surfaces violations as ERROR. A regression test counts files on disk after `generate(force=True)`, closing the broken-wheel gap that shipped in v0.1.0 (prior to this, an `is_dir()` check passed even when the wheel contained zero `.md` files — users got a workspace with an empty `commands/daily-driver/` directory).

`check(workspace_root)` returns a list of `ContractViolation`, empty iff the contract holds. The checks are written inline (one caller — `doctor.py` — so a per-kind dispatch table was more indirection than it was worth):

| Path | Check |
| --- | --- |
| `.dd-config.yaml` | parses as the pydantic `Config` model |
| `.daily-driver/` | directory exists |
| `.daily-driver/version` | file exists |
| `.daily-driver/manifest.json` | exists + valid JSON |
| `.claude/settings.local.json` | exists + valid JSON |
| `context.md`, `voice-profile.md`, `.gitignore` | file exists |
| `README.md` (and any other `ENTRIES`) | file exists |
| `.claude/commands/daily-driver/` | `>= MIN_COMMANDS` (5) `*.md` |
| `.claude/agents/daily-driver/` | `>= MIN_AGENTS` (1) `*.md` |

`.claude/commands/user/` and `.claude/agents/user/` are user territory: `init` seeds the dirs but `generate` never writes there, so they are intentionally **excluded** from the contract — `doctor --fix` cannot regenerate user territory and reporting it as ERROR would produce a stuck state.

`ENTRIES` is the authoritative list of package-managed templates rendered into the workspace root (each a `ContractEntry(src, dst)`); `generate()` iterates it, so adding a managed root file means appending one entry and nothing else. To add a contract check beyond those, edit `check()` directly, then run `make test-unit`.

## Output registers

Daily Driver writes to four distinct output registers. Picking the wrong one is the most common output bug (status text on stdout breaks `| jq`; a data payload on stderr is invisible to a pipe). Use this table to choose:

| Register | API | Stream | Purpose | Gated by |
| --- | --- | --- | --- | --- |
| Rich Console (user-facing) | `Console.print` (payload) / `Console.info` / `Console.success` / `Console.warning` / `Console.error` | stdout for `print`; stderr for status/warning/error | Human-readable status, summaries, tables, and prompts | `--quiet` mutes `print`/`info`/`success`; `warning`/`error` always show |
| Logging (diagnostics) | `dd_logging.get_logger(__name__).{debug,info,warning,error}` | stderr | Module-level diagnostic trail for debugging a run after the fact | Log level (`-q`/`-v`/`-vv` map to ERROR/INFO/DEBUG; bare = WARNING) |
| Live progress block | `core.progress.RunProgress` (Groups / Items / Phases) | stderr (same object the log handler binds) | Pinned, in-place progress for a long run that owns the terminal | TTY-only; suppressed under `--quiet` and forced to plain lines on non-TTY / unresponsive TTY / `jobs run --json` |
| Machine output (stdout contract) | bare `print(json.dumps(...))` or a single resolved path | stdout | A parseable contract for scripting (`--json`, `paths`) | Never gated -- a stable process contract |

The split keeps stdout a clean data channel: `daily-driver tracker list --json | jq` and `daily-driver jobs run --json` work because only the machine payload reaches stdout, while every status line, log line, and progress bar goes to stderr regardless of verbosity. The live progress block and the logging register deliberately share one underlying `sys.stderr` object so log lines scroll above the pinned bars on one channel.

## Console and logging

Two separate concerns, one shared Rich color theme.

**`core.console.Console`** — class-level (process-wide) two-stream Rich console:

- `_user_console` writes to **stdout**. Used for data payloads and user-facing text via `Console.print(...)`. Suppressed when `quiet_mode=True`.
- `_log_console` writes to **stderr**. Used for status/diagnostic output: `Console.info`, `Console.success`, `Console.debug` (gated by `verbose_mode`), and `Console.warning` / `Console.error` (always visible — not gated by quiet).
- `setup_for_user(quiet, verbose, no_color)` is called once from `cli/_common.configure()`. The flags drive the gates above; `--no-color` sets `color_system=None` (markup and highlight stay on so structured output still renders).
- `soft_wrap=True` on the user console when stdout is piped, so JSON / TSV consumers don't get Rich-wrapped lines.

**`core.logging`** — stdlib `logging` configured for the `daily_driver` namespace only; third-party loggers are untouched.

- `configure(verbosity)` accepts `"quiet" | "normal" | "verbose" | "debug"` and maps to `ERROR / WARNING / INFO / DEBUG`. Attaches a single plain counting `StreamHandler` (`_WarnCountingHandler`) bound to `Console.get_log_console().file` — the same `sys.stderr` object the live display's enlighten manager binds — so log lines and pinned bars interleave on one stream. A stdlib `Formatter` gives `LEVEL message` at normal verbosity and `timestamp LEVEL message` from `-v` up; bare-mode warnings read as a clean `WARNING <message>`.
- Re-entrant: removes any existing `_WarnCountingHandler` on the logger before attaching a new one, so repeated `configure()` calls (tests, in-process re-runs) don't stack levels or handlers. `propagate=False` keeps lines from leaking to the root logger.
- `live_log_window(active)` is a context manager spanning a run that owns the terminal with a live progress display. Records stream live: enlighten's scroll region carries each plain stderr write above the pinned bars, so warnings surface as they happen instead of being held to the end. The window tallies WARN+ records (counting `JobSpy:*` lines too, once they're adopted) and closes with a terse `Warnings: N (shown above)` reconciliation line in normal mode (no timestamps), written to the handler's stream after the bars tear down. A no-op when inactive or before `configure()`. The bars render on any TTY except under quiet (`-q`), which suppresses the display and the progress lines; `-v`/`-vv` only raise how much (INFO/DEBUG) scrolls above them.
- `get_logger(name)` returns child loggers under `daily_driver.<name>` — use this in `core/`, `gathers/`, `plugins/`, never bare `logging.getLogger(__name__)` outside the package root.
- `log_query_window(logger, label, since, until)` is a convenience for emitting a DEBUG (`-vv`) line describing the resolved gather window; useful for diagnosing empty-result false negatives where the bug is window math rather than extraction.

**Routing rules:**

| Output kind | API | Stream | Gated by |
| --- | --- | --- | --- |
| Data payload (table, JSON, path) | `Console.print(...)` | stdout | `--quiet` |
| Machine-readable JSON contract | bare `print(json.dumps(...))` | stdout | never (process contract) |
| Status / "ran N rows" | `Console.info(...)` | stderr | `--quiet` |
| Success summary | `Console.success(...)` | stderr | `--quiet` |
| Warning | `Console.warning(...)` | stderr | never |
| Error before exit | `Console.error(...)` | stderr | never |
| Debug trace | `Console.debug(...)` or `logger.debug(...)` | stderr | not `-vv` |
| Long-running module log | `dd_logging.get_logger(__name__).info(...)` etc. | stderr | log level |

**`core.progress`** — a domain-neutral, app-wide live-progress foundation built on enlighten; `daily-driver jobs run` is the first consumer.

- `RunProgress` is a context manager owning an `enlighten.Manager` bound to the stderr console's `sys.stderr` (the same object the log handler binds, so bars and logs interleave on one channel). It exposes Groups (a header bar counting finished children, with a red `add_subcounter` for failures), Items (one bar per source), and Phases (counter sub-rows whose `advance` is a `ProgressCallback` for worker loops).
- Every row is one `manager.counter` created once and mutated in place (`count`, `total`, `desc`, `color`); bars persist (`leave=True`) and pin at 100% on finish with the result folded into the label (`linkedin  61 found`, red on failure). No auto-refresh thread, no ticking timer — liveness is real-event-driven. `Item.start(note=...)` sets a one-time expectation note for known-slow sources.
- A single `threading.Lock` guards facade state and the non-thread-safe enlighten calls. The one blocking operation — enlighten's cursor-position query during scroll-area setup — is issued once in `__enter__`, where an unresponsive terminal is detected and downgraded to plain mode. Facade methods are hard no-ops after `__exit__` (a `_closed` flag), so a worker finishing after a Ctrl-C teardown is safe.
- `Item.progress(completed, total)` advances a bar from a uniform signal: the orchestrator binds a per-source `report(done, total)` onto `ScrapeContext` (`_run_one`), and every scraper calls `ctx.report` against its own natural unit (term×country for JobSpy/Apple, boards for Greenhouse, categories for WeWorkRemotely, a single fetch for the rest) — one code path, no library-log parsing.
- Non-TTY mode (cron, launchd, CI, pipes) and unresponsive TTYs print discrete plain lines on finish instead of pinning bars.

## Flock model

All YAML reads/writes and the focus lock use `core.locking.file_lock` (wrapping `fcntl.flock`). Read-modify-write patterns acquire the lock **before** opening the data file. Lock files are separate from data files — a crash never corrupts the data. `tracker.lock`, `generate.lock`, and the jobs sentinel `jobs.lock` live under `ephemeral_dir` (`.daily-driver/state/`), not alongside the files they guard.

**Exception — `focus.lock` is lock-as-data by design.** It is intentionally both the flock and its own JSON payload (focus start/end/reason), so it does sit in `ephemeral_dir` next to nothing it guards but itself. Payload writes are atomic (temp file + `os.replace`) so a crash mid-write never leaves a truncated body; `focus status` reads it locklessly by design, keeping the launchd check-in hook's read path cheap.

## `jobs run` resilience

`jobs run` treats `jobs.csv` itself as the run's checkpoint — there is no separate state file. `runner._JobSink` owns the run's growing row list and dedup state and is the only writer:

- **Per-source append (atomic, commit-after-write).** As each source finishes (the `on_source_result` callback off `run_all_scrapers`), `_JobSink.append_source` classifies that source's rows, then — under one held `_rows_lock` + `jobs.lock` critical section — appends them to `jobs.csv` AND extends `rows`. Only after the file write succeeds does it commit the dedup-set growth, the funnel, and the run totals, so the in-memory state never claims rows the disk lacks (no phantom dedup entries). A write failure raises `ScraperError`; the `on_source_result` wrapper in `run()` catches it, marks just that source failed, and lets the remaining sources finish (per-source isolation).
- **Enrichment as in-place flush.** Enrichers replace row slots in `_JobSink.rows`; `_JobSink.flush()` snapshots `rows` and rewrites the whole file through `csv_io.atomic_write_rows` under one held `_rows_lock` + `jobs.lock`. Unknown / hand-added columns ride along POSITIONALLY via a parallel `row_extras` list (1:1 with `rows`, never keyed by Link — so every row keeps its own extras even with a blank or duplicate Link). The flush is atomic relative to a concurrent append; the lock is held only for the rewrite, never across LLM calls. `flush_periodic()` is the in-loop hook (after each phase, every ~25 LLM results): an `OSError` there is a **PERSISTENCE failure** — sets the sticky `persistence_degraded` flag, logs once, but enrichment continues. At run end `run()` warns if degraded and the final `flush()` propagates its failure loudly (after the completion manifest is written), so a silent disk problem can't masquerade as healthy.
- **Backfill reuses the driver.** `jobs backfill` shares the same `_JobSink` + `_enrich_wave` driver to resume empty rows. It reads and rewrites jobs.csv ALL inside one `jobs.lock` window held for the whole read -> enrich -> rewrite (it rewrites from a snapshot, so it cannot drop the lock mid-enrichment) and builds the sink with `external_lock_held=True` so the per-flush sections don't re-take the non-reentrant flock. It also sets `skip_flush_if_unchanged` (no rewrite, and a lazy `pre_write_hook` backup, only when content changed) and mirrors `run()`'s degraded-save warning, naming the backup on a final-flush `OSError`.
- **Scrape/enrich overlap (shared budget by attempted identity).** When an Apple (phase 2) scrape follows phase 1, the phase-1 rows enrich in a background **wave** while Apple still scrapes; Apple's rows get a second wave afterward. The wave-1 thread and the appending coordinator thread serialize on `_JobSink._rows_lock`; slot replacement is GIL-atomic and never overlaps an appended index. Budgets are shared running totals: wave 1 reports the identities it ATTEMPTED (`attempted` out-param of `enrich_product_and_fit_concurrently` — `fit_urls` and the UNIQUE `product_companies`, since the company cap counts companies not jobs), wave 2 excludes those (`exclude_fit_urls` / `exclude_companies`) so an attempted row — success OR failure — is never retried or double-charged, and sizes its budget as `cap − len(attempted)`. Backfill is the retry path for wave-1 failures.
- **Interrupts.** `SIGTERM` (a scheduled run's stop signal) is routed through the same graceful drain/flush path as Ctrl-C: the CLI installs a run-scoped handler (`enrichment/_shared.install_sigterm_handler`, main-thread-only, like the SIGINT notifier) that raises `KeyboardInterrupt`. The CLI exits `130` for `SIGINT`, `143` for `SIGTERM`. The interrupt-path flush is guarded — an `OSError` there is logged, not allowed to replace the `KeyboardInterrupt` (which would degrade the exit code and skip the manifest).
- **Manifest on every exit.** `run()` is a thin wrapper around `_run_impl` holding a `_RunState`; ANY exit that escapes — a Ctrl-C/SIGTERM during scraping or the overlap window, the csv-init `OSError`, or any crash — writes `jobs-last-run.json` with `interrupted=True` at the phase reached before re-raising. Clean completion writes `interrupted=False`. The manifest carries `phase_reached` (`scraping` / `detail` / `enrichment` / `complete`) and `interrupted`; `jobs status` prints a recovery line when the last run was cut short. (dry-run writes no manifest — it has no sink and returns before the write.)

## Extensibility

### Without forking

- **Tracker categories** — config-driven. Add a key under `tracker.categories` in `.dd-config.yaml`. See [configuration.md](../configuration.md#customization).
- **`plugins.job_search.*`** — sources, filters, locations, enrichment tuning all live in config.
- **Your own slash commands / agents** — any `.md` file under `.claude/commands/` or `.claude/agents/` outside the `daily-driver/` subdir is yours and never touched. See [configuration.md#customization](../configuration.md#customization).

### Requires a fork

- New core subcommand — static `_COMMANDS` table in `cli/cli.py`; no runtime discovery.
- New config fields — Pydantic model in `core.config_models`. `extra="forbid"` is strict.
- New plugin — see [extending.md](extending.md) and "Plugins" below.

### Plugins

`source/daily_driver/plugins/` holds feature slices that core wires through a static registry — no runtime discovery. `job_search` is the one shipped plugin; its config, CLI, scraper sources, scheduler, and doctor checks all live under `plugins/job_search/`.

- **Registry.** `plugins/__init__.py` exposes `PLUGINS: tuple[Plugin, ...]`, listed explicitly. `_validate_registry` runs at import and `find_spec`-checks every declared hook module (command, scheduled-jobs builder, doctor checks, package-data sources) so a typo'd path fails at startup, not on first use.
- **Contract.** `plugins/_base.py:Plugin` is a frozen dataclass describing a plugin without importing its implementation. Fields: `command_name` / `command_module` / `command_help` (CLI verb, lazily imported on dispatch); `config_model` (a `BaseModel` validating the plugin's `plugins.<name>` config namespace); `scheduled_jobs_builder` (dotted path to `build_scheduled_jobs(ctx)`); `doctor_checks` (dotted path to `run_checks(workspace)`); `package_data_dirs` (tuple of `PackageDataDir`, the slash-command / agent `.md` dirs copied into the workspace `.claude/` tree).
- **Core iterates `PLUGINS`.** `core.scheduler`, `core.doctor`, and `core.generate` walk the registry (importing `PLUGINS` lazily inside the function so core stays import-light) and pull each plugin's contribution. `PluginsConfig` (in `plugins/config.py`) is assembled from `PLUGINS` so the root `Config` carries no hardcoded plugin namespace; it is `extra="forbid"`, matching the strict root.
- **Package-data extension point.** A plugin that ships slash-commands sets `package_data_dirs`; `core.generate` appends them to core's baseline (`commands/daily-driver`, `agents/daily-driver`) and applies the same SHA-256 manifest + stale-removal per dest. `job_search` ships none (`package_data_dirs = ()`), but the path is exercised by a synthetic-plugin test.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Business logic in `cli/commands/*.py` | Move to `core/<name>.py` |
| `subprocess.run(...)` in `core/` or `cli/` | Add a wrapper in `integrations/` |
| Reading/writing YAML without `file_lock` | Always guard with `core.locking.file_lock(path)` |
| Editing `.claude/*/daily-driver/` expecting persistence | That subdir is package-managed; `--fix` preserves edits via manifest but `--reset` overwrites |
| Unknown keys under the config root | Root is `extra="forbid"`. Nest under `plugins.<namespace>:` |
| Bare `print(...)` for status text | Use `Console.info/success/warning/error` from `core/console.py` — those route to stderr. Reserve bare `print()` for stdout data contracts (machine-readable JSON) |
| `logging.getLogger(__name__)` outside `daily_driver` root | Use `dd_logging.get_logger(__name__)` so the logger is under the `daily_driver.*` namespace and inherits the configured handler |

## Common tasks

| Task | Solution |
| --- | --- |
| Access workspace | `Workspace.discover_or_fail(override=args.workspace)` at command entry |
| Lock a file | `with file_lock(path): ...` — always read-modify-write inside the lock |
| Shell out | Add a wrapper in `integrations/`. Never `subprocess.run` from `cli/` or `core/` |
| Bundle a template | Drop into `source/daily_driver/resources/templates/`, access via `importlib.resources` |
| Add a subcommand | See [extending.md](extending.md) |
| Add a scraper source | See [extending.md](extending.md) |

## Dev setup and make targets

```bash
git clone https://github.com/skoonin/daily-driver.git
cd daily-driver
make setup           # .venv + pip install -e .[dev] + pre-commit hooks
make test
```

| Target | Purpose |
| --- | --- |
| `make setup` | venv + editable install + pre-commit hooks |
| `make test` | Full tox envlist (lint + type + py311 + py312 + coverage), matches CI |
| `make test-quick` | py311 only — fast inner loop |
| `make test-unit` / `test-cli` / `test-e2e` | Scoped suites |
| `make test-cov` | Full suite + coverage report + HTML |
| `make lint` | black + isort + flake8 + mypy |
| `make format` | Auto-format (black + isort) |
| `make type` | mypy only |
| `make build` | sdist + wheel into `dist/` |
| `make release` / `release-push` | See [releasing.md](releasing.md) |
| `make clean` / `distclean` | Remove build artifacts (and `.venv` for distclean) |

See [releasing.md](releasing.md) for the release workflow and [extending.md](extending.md) for adding subcommands or scraper sources.

## Calendar (icalBuddy) setup

`daily-driver gather calendar` shells out to `icalBuddy` to read macOS
Calendar events. It needs three things to return data:

1. **Binary on PATH.** `brew install ical-buddy`. Check with `which icalBuddy`.
2. **Calendar access for the terminal.** macOS gates Calendar access per
   parent process. Open *System Settings -> Privacy & Security -> Calendars*
   and toggle on the terminal you launch `daily-driver` from (Terminal,
   iTerm2, VS Code, etc.). The first run after granting access may require
   restarting the terminal.
3. **icalBuddy preference plist** (optional but recommended). Without a
   plist, icalBuddy will read all calendars including spammy subscribed
   ones. Create `~/Library/Preferences/com.hasseg.icalBuddy.plist` to scope
   the calendars and time format. Minimal example:

   ```bash
   /usr/libexec/PlistBuddy -c "Add :includeCals array" ~/Library/Preferences/com.hasseg.icalBuddy.plist
   /usr/libexec/PlistBuddy -c "Add :includeCals: string 'Work'" ~/Library/Preferences/com.hasseg.icalBuddy.plist
   ```

   List your calendars with `icalBuddy calendars` and substitute their
   names. See `man icalBuddy` for the full key reference.

If `gather calendar` is silent or warns about a non-zero exit, run the
underlying command directly to see the raw error — the gather code surfaces
icalBuddy's stderr but elides multi-line output:

```bash
icalBuddy -nc -df '%Y-%m-%d' -tf '%H:%M' eventsToday
```
