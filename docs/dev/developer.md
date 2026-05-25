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
│   ├── logging.py           # stdlib logger with RichHandler on stderr
│   └── (no subprocess calls, no TTY/clock/network deps — pure unit-testable)
├── gathers/                 # read-only readers: calendar, git, sessions, notes
├── scraper/                 # comp, parsing, csv_io, enrichment, runner + sources/{remoteok,…,apple}.py
├── integrations/            # subprocess wrappers: claude_cli, clipboard, launchd
├── launchd/                 # PACKAGE DATA — plist Jinja templates
├── commands/daily-driver/   # PACKAGE DATA — shipped slash commands
├── agents/daily-driver/     # PACKAGE DATA — shipped agents
├── templates/               # PACKAGE DATA — scaffold + settings Jinja templates
└── plugins/                 # forward-compat namespace; empty in v0.1.0
```

### Layer rules

- **`cli/`** — argparse + Rich only. `add_parser(subparsers, parents)` + `run(args) -> int`. Non-trivial logic belongs in `core/<same-name>.py`. Use `core.console.Console` for user-facing status / errors; reserve bare `print()` for machine-readable stdout payloads (JSON, single resolved paths).
- **`core/`** — business logic. No subprocess, no TTY, no network. Unit-testable in isolation.
- **`gathers/`** — typed readers returning plain dataclasses. No YAML, no printing.
- **`scraper/`** — split into focused modules: `comp.py`, `parsing.py`, `csv_io.py`, `enrichment.py`, `runner.py` (orchestration + filters + dedup), and `sources/<name>.py` (one file per source). `sources/__init__.py` registers each source in `SCRAPERS` / `SOURCE_REGISTRY`.
- **`integrations/`** — the single subprocess boundary. Every `claude`, `pbcopy`, `launchctl` call goes through here. Tests monkeypatch one module instead of many.

## Runtime flow

`daily-driver` (console entry) → `daily_driver.cli.cli.app()`:

1. Argparse tree built from static `_COMMANDS` table. Subcommand modules are imported lazily (`importlib.import_module`) — startup stays cheap.
2. Global flags (`--workspace`, `--verbose`/`-v`/`-vv`, `--quiet`, `--no-color`) are registered via `cli/_common.py:add_global_flags(parser)` on both the top-level parser and each leaf, AFTER local args, so they render under a "global options" group at the bottom of `--help`. All globals use `default=argparse.SUPPRESS`; reads must use `getattr(args, name, default)`.
3. `cli/_common.py:configure(args)` runs once after parse: it calls `core.logging.configure(verbosity)` (mapping `-q`/`-v`/`-vv` to `ERROR`/`INFO`/`DEBUG`; bare invocation is `WARNING`), then `Console.setup_for_user(quiet, verbose, no_color)`. Both helpers are idempotent — second calls remove the existing RichHandler before re-attaching.
4. Most subcommands call `Workspace.discover_or_fail(override=args.workspace)`. Precedence: `--workspace` flag, upward search for `.dd-config.yaml`, fail with `WorkspaceError`.
5. `core.config.load()` parses YAML (`safe_load`) and validates against `core.config_models.Config`. Pydantic v2, `extra="forbid"` at every nesting level. Invalid config fails at parse, not mid-command.
6. `core.version_stamp` compares the workspace's stamped version against `daily_driver.__version__`. On mismatch: `doctor --fix` regenerates; other subcommands proceed without touching the filesystem.
7. Subcommand `run(args)` does the work and returns an int exit code. Errors are caught at the top of `app()`: at `-v` or higher the daily_driver logger emits a full `logger.exception()` traceback to stderr; otherwise `Console.error(str(exc))` prints a single-line message. Non-zero exit either way — no tracebacks on happy-path failures.

Scheduled invocations go through the same entry point. `scheduler install` renders `launchd/<job>.plist.j2` with workspace-specific paths and calls `launchctl load`. stdout/stderr redirect to `.daily-driver/state/logs/launchd-<job>.{out,err}` via the plist.

## Generate lifecycle

`generate(workspace, force=False)` copies `commands/daily-driver/*.md`, `agents/daily-driver/*.md`, and a rendered `settings.local.json` into the workspace `.claude/` tree. Only those three paths are ever touched.

Key invariants:

- **Version stamp written last.** A crash mid-generate leaves the stamp stale; the next invocation re-runs. Idempotent by construction.
- **SHA-256 manifest guards user edits.** `_copy_package_md` checks the manifest before overwriting any managed `.md` file. A user edit flips the hash; subsequent generates skip that file and log a warning. `doctor --fix` respects this; `doctor --reset` is the nuclear option that overwrites.
- **`settings.local.json` merges instead of overwriting.** Package-managed top-level keys are refreshed from the template; user-added top-level keys are preserved.
- **Concurrency.** Generate acquires an exclusive `generate.lock` under `ephemeral_dir` and re-checks drift inside the lock (double-checked locking).

## Init contract

`core/contract.py` codifies every artifact `daily-driver init` promises to produce. `doctor` runs `contract.check()` and surfaces violations as ERROR. A regression test counts files on disk after `generate(force=True)`, closing the broken-wheel gap that shipped in v0.1.0 (prior to this, an `is_dir()` check passed even when the wheel contained zero `.md` files — users got a workspace with an empty `commands/daily-driver/` directory).

| Path | Kind |
| --- | --- |
| `.dd-config.yaml` | `parses_config` |
| `.daily-driver/` | `exists_dir` |
| `.daily-driver/version` | `exists_file` |
| `.daily-driver/manifest.json` | `json_valid` |
| `.claude/settings.local.json` | `json_valid` |
| `context.md`, `voice-profile.md`, `.gitignore` | `exists_file` |
| `.claude/commands/daily-driver/` | `count_gte >= 5` |
| `.claude/agents/daily-driver/` | `count_gte >= 1` |

`.claude/commands/user/` and `.claude/agents/user/` are user territory: `init` seeds the dirs but `generate` never writes there, so they are intentionally **excluded** from the contract — `doctor --fix` cannot regenerate user territory and reporting it as ERROR would produce a stuck state.

Validation kinds: `exists_file`, `exists_dir`, `parses_yaml`, `parses_config`, `json_valid`, `count_gte`.

To add an entry: append to `ENTRIES` in `contract.py`, ensure `init`/`generate` produces the path, run `make test-unit`.

## Console and logging

Two separate concerns, one shared Rich color theme.

**`core.console.Console`** — class-level (process-wide) two-stream Rich console:

- `_user_console` writes to **stdout**. Used for data payloads and user-facing text via `Console.print(...)`. Suppressed when `quiet_mode=True`.
- `_log_console` writes to **stderr**. Used for status/diagnostic output: `Console.info`, `Console.success`, `Console.debug` (gated by `verbose_mode`), and `Console.warning` / `Console.error` (always visible — not gated by quiet).
- `setup_for_user(quiet, verbose, no_color)` is called once from `cli/_common.configure()`. The flags drive the gates above; `--no-color` sets `color_system=None` (markup and highlight stay on so structured output still renders).
- `soft_wrap=True` on the user console when stdout is piped, so JSON / TSV consumers don't get Rich-wrapped lines.

**`core.logging`** — stdlib `logging` configured for the `daily_driver` namespace only; third-party loggers are untouched.

- `configure(verbosity)` accepts `"quiet" | "normal" | "verbose" | "debug"` and maps to `ERROR / WARNING / INFO / DEBUG`. Attaches a single `RichHandler` whose console is `Console.get_log_console()` so log lines and `Console.*` lines share the same stderr stream and theme.
- Re-entrant: removes any existing `RichHandler` on the logger before attaching a new one, so repeated `configure()` calls (tests, in-process re-runs) don't stack levels or handlers. `propagate=False` keeps lines from leaking to the root logger.
- `get_logger(name)` returns child loggers under `daily_driver.<name>` — use this in `core/`, `scraper/`, `gathers/`, never bare `logging.getLogger(__name__)` outside the package root.
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

The split exists so `daily-driver tracker list --json | jq …` and `daily-driver paths daily-plan | xargs $EDITOR` work cleanly: only the data payload hits stdout; every status line goes to stderr regardless of verbosity.

## Flock model

All YAML reads/writes and the focus lock use `core.locking.file_lock` (wrapping `fcntl.flock`). Read-modify-write patterns acquire the lock **before** opening the data file. Lock files are separate from data files — a crash never corrupts the data. `tracker.lock` and `generate.lock` live under `ephemeral_dir` (`.daily-driver/state/`), not alongside the files they guard.

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
