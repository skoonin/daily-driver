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
│   └── commands/            # one module per subcommand; argparse + presentation only
├── core/                    # workspace, config, tracker, locking, generate, scheduler...
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

- **`cli/`** — argparse + Rich only. `add_parser(subparsers, parents)` + `run(args) -> int`. Non-trivial logic belongs in `core/<same-name>.py`.
- **`core/`** — business logic. No subprocess, no TTY, no network. Unit-testable in isolation.
- **`gathers/`** — typed readers returning plain dataclasses. No YAML, no printing.
- **`scraper/`** — split into focused modules: `comp.py`, `parsing.py`, `csv_io.py`, `enrichment.py`, `runner.py` (orchestration + filters + dedup), and `sources/<name>.py` (one file per source). `sources/__init__.py` registers each source in `SCRAPERS` / `SOURCE_REGISTRY`.
- **`integrations/`** — the single subprocess boundary. Every `claude`, `pbcopy`, `launchctl` call goes through here. Tests monkeypatch one module instead of many.

## Runtime flow

`daily-driver` (console entry) → `daily_driver.cli.cli.app()`:

1. Argparse tree built from static `_COMMANDS` table. Subcommand modules are imported lazily (`importlib.import_module`) — startup stays cheap.
2. Global flags (`--workspace`, `--verbose`, `--quiet`, `--no-color`) live on a shared parent parser.
3. Most subcommands call `Workspace.discover_or_fail(override=args.workspace)`. Precedence: `--workspace` flag, upward search for `.dd-config.yaml`, fail with `WorkspaceError`.
4. `core.config.load()` parses YAML (`safe_load`) and validates against `core.config_models.Config`. Pydantic v2, `extra="forbid"` at every nesting level. Invalid config fails at parse, not mid-command.
5. `core.version_stamp` compares the workspace's stamped version against `daily_driver.__version__`. On mismatch: `doctor --fix` regenerates; other subcommands proceed without touching the filesystem.
6. Subcommand `run(args)` does the work and returns an int exit code. Errors are caught at the top of `app()`, logged, and turned into non-zero exits with a Rich-formatted stderr message — no tracebacks on happy-path failures.

Scheduled invocations go through the same entry point. `install-scheduler` renders `launchd/<job>.plist.j2` with workspace-specific paths and calls `launchctl load`. stdout/stderr redirect to `.daily-driver/state/logs/launchd-<job>.{out,err}` via the plist.

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
|------|------|
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

## Flock model

All YAML reads/writes and the focus lock use `core.locking.file_lock` (wrapping `fcntl.flock`). Read-modify-write patterns acquire the lock **before** opening the data file. Lock files are separate from data files — a crash never corrupts the data. `tracker.lock` and `generate.lock` live under `ephemeral_dir` (`.daily-driver/state/`), not alongside the files they guard.

## Extensibility

### Without forking

- **Tracker categories** — config-driven. Add a key under `tracker.categories` in `.dd-config.yaml`. See [configuration.md](configuration.md).
- **`plugins.job_search.*`** — sources, filters, locations, enrichment tuning all live in config.
- **Your own slash commands / agents** — any `.md` file under `.claude/commands/` or `.claude/agents/` outside the `daily-driver/` subdir is yours and never touched. See [customization.md](customization.md).

### Requires a fork

- New subcommand — static `_COMMANDS` table in `cli/cli.py`; no runtime plugin loader.
- New scraper source — add `scraper/sources/<name>.py` with a `scrape_<name>(config) -> list[dict]` function and register it in `scraper/sources/__init__.py:SCRAPERS`. See [extending.md](extending.md).
- New config fields — Pydantic model in `core.config_models`. `extra="forbid"` is strict.

### Why `plugins/` is empty

`source/daily_driver/plugins/` is a forward-compat seam. In v0.1.0 it holds no code. Its purpose: scoping feature-specific config under `plugins.<namespace>:` today so a future release can add dynamic loading (discovering `daily_driver.plugins` entry-points at startup) without repainting the config model. Adding a loader now, without a second concrete plugin to validate the API, would freeze the wrong shape.

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Business logic in `cli/commands/*.py` | Move to `core/<name>.py` |
| `subprocess.run(...)` in `core/` or `cli/` | Add a wrapper in `integrations/` |
| Reading/writing YAML without `file_lock` | Always guard with `core.locking.file_lock(path)` |
| Editing `.claude/*/daily-driver/` expecting persistence | That subdir is package-managed; `--fix` preserves edits via manifest but `--reset` overwrites |
| Unknown keys under the config root | Root is `extra="forbid"`. Nest under `plugins.<namespace>:` |
| Using `print(...)` | Use Rich `console.print()` from `cli/commands/_utils.py` |

## Common tasks

| Task | Solution |
|------|----------|
| Access workspace | `Workspace.discover_or_fail(override=args.workspace)` at command entry |
| Lock a file | `with file_lock(path): ...` — always read-modify-write inside the lock |
| Shell out | Add a wrapper in `integrations/`. Never `subprocess.run` from `cli/` or `core/` |
| Bundle a template | Drop into `source/daily_driver/templates/`, access via `importlib.resources` |
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
|--------|---------|
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
   /usr/libexec/PlistBuddy -c \
     "Add :includeCals array" \
     ~/Library/Preferences/com.hasseg.icalBuddy.plist
   /usr/libexec/PlistBuddy -c \
     "Add :includeCals: string 'Work'" \
     ~/Library/Preferences/com.hasseg.icalBuddy.plist
   ```

   List your calendars with `icalBuddy calendars` and substitute their
   names. See `man icalBuddy` for the full key reference.

If `gather calendar` is silent or warns about a non-zero exit, run the
underlying command directly to see the raw error -- the gather code surfaces
icalBuddy's stderr but elides multi-line output:

```bash
icalBuddy -nc -df '%Y-%m-%d' -tf '%H:%M' eventsToday
```
