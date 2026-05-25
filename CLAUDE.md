# Daily Driver

Daily Driver is a personal assistant for driving daily work —
professional tasks, job search, errands, and anything else you need to track.
It helps users overcome task paralysis and task overwhelming by keeping a
durable record on disk and giving Claude the context it needs to help.

**The program owns the durable record** (tracker.yaml, daily notes, jobs.csv,
focus lock, config). **Claude is the conversational layer** — helping users
understand priorities, stay motivated, and discern patterns. Claude is not a
reliable data store; data lives in the program.

This repo builds the `daily-driver` console command. All workspace state lives
in a user-scaffolded workspace directory on disk; a companion `claude` session
owns the conversation.

Jobs search is one plugin. The tracker is designed to hold tasks of any kind:
errands, project milestones, support tickets, job applications. See
`docs/dev/developer.md` for product framing and architecture.

## Install for development

```bash
make setup    # .venv + pip install -e .[dev] + pre-commit hooks
make test     # full tox envlist (matches CI: lint + type + py311 + py312 + coverage)
```

## Command surface

End-user reference: `docs/commands.md`. Short list:

- `daily-driver init <path>` — scaffold a workspace.
- `daily-driver doctor [--fix | --reset]` — verify / repair workspace.
- `daily-driver tracker {add,list,update,stats,follow-ups}` — manage tracked
  tasks of any kind (jobs, errands, projects, contacts, ...) via a
  config-driven category system.
- `daily-driver status` — dashboard table (Rich or JSON).
- `daily-driver focus {on,off,status}` — flock-backed check-in suppression.
- `daily-driver day-start | day-end | check-in` — interactive Claude-session
  launchers (read-only scaffold + `claude --add-dir workspace`).
- `daily-driver summary --range SPEC` — headless period summary via `claude`;
  prints to stdout and copies to clipboard.
- `daily-driver voice-update --from PATH` — headless rewrite of
  `voice-profile.md` from writing samples.
- `daily-driver jobs {run,status,prune}` — search the configured job
  boards, show last-run metadata, or archive stale rows from `jobs.csv`.
- `daily-driver paths [<kind>] [--json]` — print workspace-resolved paths
  (output, state, daily plan/notes).
- `daily-driver gather {calendar,git,sessions,notes} [--json]` — read
  external state for downstream commands.
- `daily-driver scheduler {install,uninstall,status}` — macOS launchd
  plist install / remove / inspection.

## Architecture

All code lives under `source/daily_driver/`. Read `docs/dev/developer.md` for the
architecture map, runtime flow, init contract, generate lifecycle, and
extensibility rules. Recipes for adding subcommands or scraper sources live
in `docs/dev/extending.md`. Release workflow is in `docs/dev/releasing.md`.

Key conventions:

- **CLI is presentation-only.** `cli/commands/<name>.py` does argparse +
  Rich; non-trivial logic lives in `core/<name>.py`.
- **Subprocess calls funnel through `integrations/`.** `claude`, `pbcopy`,
  `launchctl` — tests monkeypatch one spot, not many.
- **Workspace writes are flock-guarded.** `core.locking.file_lock` wraps
  `fcntl.flock` for YAML reads + writes and the focus lock.
- **`.claude/*/daily-driver/` is package-managed.** Generated from wheel
  package-data on `init` and on version drift. `doctor --fix` preserves
  user-edited managed files (SHA-256 manifest); `doctor --reset` overwrites.
  Top-level `.claude/commands/` and `.claude/agents/` are user territory.
- **Plugin namespacing.** `plugins.job_search:` in `.dd-config.yaml` is
  the one shipped plugin namespace; the root Config is pydantic
  `extra="forbid"`.

## Testing

- `make test` — full tox envlist (matches CI).
- `make test-quick` — py311 only, fast inner loop.
- `make test-unit` / `test-cli` / `test-e2e` — scoped suites.
- `make lint` / `make format` / `make type` — black + isort + flake8 + mypy.

Test modules mirror source layout: `tests/test_core/`, `tests/test_cli/`,
`tests/test_scraper/`, `tests/test_gathers/`, `tests/test_integrations/`.

## Release

- `make release VERSION=X.Y.Z` — verify clean tree, run py311+py312, run
  install smoke, build wheel/sdist, rewrite CHANGELOG `[Unreleased]` →
  `[X.Y.Z]`, bump `__version__`, commit `release: vX.Y.Z`, tag.
- `make release-push` — push commit + tag (separate deliberate step).
- `release.yaml` CI fires on tag, builds and attaches artifacts to the
  GitHub Release.
