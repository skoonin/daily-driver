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

Job search is one plugin, off by default: `init --job-search` (or a `plugins.job_search` block in `.dd-config.yaml`) switches it on. The tracker is designed to hold tasks of any kind: errands, project milestones, support tickets, job applications. See `docs/dev/developer.md` for product framing and architecture.

## Install for development

```bash
make setup-dev  # .venv + pip install -e .[dev] + pre-commit hooks
make test       # full tox envlist (see tox.ini); the same set CI runs
                # needs python3.11 and python3.12 on PATH; tox fails on a missing one
```

## Command surface

End-user reference: `docs/commands.md`. Short list:

- `daily-driver init <path> [--job-search]` — scaffold a workspace; `--job-search` switches job search on.
- `daily-driver doctor [--fix | --reset]` — verify / repair workspace.
- `daily-driver config defaults [--plugin NAME]` — print the settings file `init` writes, with every default.
- `daily-driver tracker {add,list,update,show,delete,prune,stats,follow-ups}` — manage tracked tasks of any kind (jobs, errands, projects, contacts, ...) via free-form categories (`tracker.categories` adds required fields).
- `daily-driver status` — dashboard table (Rich or JSON).
- `daily-driver focus {on,off,status}` — flock-backed check-in suppression.
- `daily-driver day-start | day-end | check-in` — interactive session launchers (`start` and `end` are aliases). They run on the provider that has the `interactive` task (default `claude`, launched with the read-only scaffold + `--add-dir workspace`).
- `daily-driver resume` — reattach to the workspace's most recent session through the provider's `resume` template (`claude --resume <id>` for claude) when a launcher tab is closed or lost. It refuses a session that another provider started.
- `daily-driver summary --range SPEC` — period summary from the provider that has the `summary` task (an agent task: the program lists the files, the provider reads them); prints to stdout and copies to clipboard.
- `daily-driver voice-update --from PATH` — rewrite of `voice-profile.md` from writing samples, run by the provider that has the `voice_update` task (a text task).
- `daily-driver jobs {run,backfill,promote,discover-boards,status,prune,verify}` —
  search the configured job boards, re-enrich empty fields on existing rows,
  promote a row into the tracker, show last-run metadata, archive stale rows, or
  URL-check row liveness in `jobs.csv`. Not a command while job search is off.
- `daily-driver paths <kind> [--json]` — print workspace-resolved paths
  (root, state, tracker, daily plan/notes).
- `daily-driver gather {calendar,git} [--json]` — read
  external state for downstream commands.
- `daily-driver scheduler {install,uninstall,status}` — macOS launchd
  plist install / remove / inspection.
- `daily-driver calendar sync` — write today's plan time blocks to a local macOS Calendar (needs `calendar.sync_enabled: true`).
- `daily-driver help [commands|statuses|categories|dates|<plugin topic>]` — reference for subcommands and discoverable values. A plugin that is off adds nothing to it (job search adds `sources`).

## Architecture

All code lives under `source/daily_driver/`. Read `docs/dev/developer.md` for the architecture map, runtime flow, init contract, generate lifecycle, and extensibility rules. Recipes for adding subcommands, plugins, or scraper sources live in `docs/dev/extending.md`. Release workflow is in `docs/dev/releasing.md`.

Key conventions:

- **CLI is presentation-only.** `cli/commands/<name>.py` does argparse +
  Rich; non-trivial logic lives in `core/<name>.py`.
- **Subprocess calls funnel through `integrations/`.** AI programs and servers
  (`integrations/provider.py`), `pbcopy`,
  `launchctl` — tests monkeypatch one spot, not many.
- **AI is provider-agnostic, and providers are data.** `tasks.py` names the tasks
  (`interactive`, `summary`, `voice_update`, plus each plugin's own). A provider
  is command templates or an OpenAI-compatible HTTP endpoint, set under
  `ai.providers` and merged over the built-ins in
  `resources/templates/providers.default.yaml` (`claude`, `opencode`, `lmstudio`,
  `ollama`). `core.providers.provider_for` picks the provider for a task, and
  `integrations.provider.invoke` runs it. A provider claims core tasks with
  `tasks`; a task nobody claims runs on `claude`. No code branches on a provider
  name. A plugin declares tasks on `Plugin.tasks` and routes them under
  `plugins.<name>.provider` and `plugins.<name>.tasks`. See
  `docs/dev/developer.md` and `docs/dev/extending.md`.
- **Workspace writes are flock-guarded.** `core.locking.file_lock` wraps
  `fcntl.flock` for YAML reads + writes and the focus lock.
- **These paths are package-managed:** `.claude/commands/daily-driver/`, `.claude/agents/daily-driver/`, `.claude/hooks/`, and the workspace `README.md`. Generated from wheel package-data on `init` and on version drift. `doctor --fix` preserves user-edited managed files (SHA-256 manifest) but deletes `.md` files you add to the commands and agents directories and `.sh` files you add to hooks. `doctor --reset` wipes and rewrites those directories and overwrites the workspace `README.md`. Top-level `.claude/commands/` and `.claude/agents/` are user territory. Full rule: `docs/configuration.md` (`.claude/` ownership).
- **Plugins are a real boundary.** Each plugin lives under
  `source/daily_driver/plugins/<name>/` and exposes a `PLUGIN` (`plugins/_base.py`
  `Plugin` dataclass) registered in the static `plugins.PLUGINS` tuple — no runtime
  discovery. Core iterates `PLUGINS` (lazily, by dotted path) for CLI command, config
  model, scheduled jobs, doctor checks, and package data. `job_search` is the one
  shipped plugin; its config namespace is `plugins.job_search:` in `.dd-config.yaml`.
  The root Config and `PluginsConfig` are both pydantic `extra="forbid"`; the config load warns about an unknown key, ignores it, and `doctor` fails on it. Core and CLI code reach a plugin only through `PLUGINS` and its `Plugin` fields, never by importing its modules, and a plugin's settings, including its schedule, live under `plugins.<name>`.
- **Plugins are opt-in per workspace.** `plugins.enabled_plugins(config)` is the
  one on/off answer: a plugin is on when its `plugins.<name>` block exists and
  does not set `enabled: false`. Generated prompt sections, package data, doctor
  checks, launchd jobs, and the plugin's CLI command all ask it. Shipped `.md`
  files and the config template are Jinja (`core/templating.py`) with
  `{% if "<name>" in plugins %}` blocks; the version stamp records the plugins
  on, so switching one is drift that `doctor --fix` applies.

## Testing

- `make test` — full tox envlist (matches CI).
- `make test-quick` — py311 only, fast inner loop.
- `make test-unit` / `test-cli` / `test-e2e` — scoped suites.
- `make lint` / `make format` / `make type` — black + isort + flake8 + mypy.
- pre-commit runs black, isort, flake8, pyupgrade, autoflake, and the pre-commit-hooks hygiene checks (whitespace, end of file, YAML and TOML syntax, debug statements).
- CI runs black, isort, flake8, mypy, and pytest (which includes tests that validate the shipped config template). pyupgrade and autoflake have no CI counterpart; mypy has no pre-commit hook.
- Tests marked `smoke` hit live job-board URLs. pytest `addopts` excludes them and nothing runs them on a schedule; run `pytest -m smoke tests/test_plugins/job_search/scraper/test_e2e_smoke.py` by hand.

Test modules mirror source layout: `tests/test_core/`, `tests/test_cli/`,
`tests/test_plugins/job_search/` (incl. `scraper/`), `tests/test_gathers/`,
`tests/test_integrations/`.

## Release

- **Branches:** `dev` is the integration trunk — feature branches cut off `dev` and merge back to it, and `dev` carries the `X.Y.Z-dev` working marker (`pipx install --force 'git+https://github.com/skoonin/daily-driver.git@dev'` to test). `main` holds only tagged releases. Cut a release from a `release/X.Y.Z` branch off `dev` (run `make release` there — it refuses any branch that is not `release/*`), then fast-forward `main` to the tag with `git push origin vX.Y.Z^{commit}:refs/heads/main`. An optional `release/X.Y.Z` → `main` pull request is for visibility only: do not merge it with a GitHub merge button, because a merge commit leaves `main` off the tag and a rebase or squash merge rewrites the commit hash. After the tag reaches `main`, fast-forward `dev` to the tag and bump its `-dev` marker. `main` is only ever updated by a release fast-forward, never a direct `dev` → `main` merge. Full flow in `docs/dev/releasing.md`.
- `make release VERSION=X.Y.Z` — verify release branch + clean tree, run
  py311+py312, run install smoke, build wheel/sdist, rewrite CHANGELOG
  `[Unreleased]` → `[X.Y.Z]`, bump `__version__`, commit `release: vX.Y.Z`, tag.
- `make release-push` — push commit + tag (separate deliberate step).
- `release.yaml` CI fires on tag, builds and attaches artifacts to the
  GitHub Release.
