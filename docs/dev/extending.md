# Extending

Three patterns: adding a subcommand, adding a plugin, and adding a scraper source.

## Adding a subcommand

Two shapes: pure-Python (argparse + `core/`) and Claude-launcher (spawn a nested `claude` session).

### Pure-Python

Create `source/daily_driver/cli/commands/ping.py`:

```python
from __future__ import annotations
import argparse

def add_parser(subparsers, parents):
    parser = subparsers.add_parser("ping", parents=parents, help="Print pong.")
    parser.add_argument("--loud", action="store_true")
    parser.set_defaults(func=run)
    return parser

def run(args: argparse.Namespace) -> int:
    print("PONG" if args.loud else "pong")
    return 0
```

Register in `_COMMANDS` in `cli/cli.py`:

```python
("ping", "daily_driver.cli.commands.ping"),
```

CLI module stays presentation-only. Non-trivial logic goes in `core/ping.py`.

### Claude launcher

Create the shipped slash-command file at `source/daily_driver/resources/slash_commands/daily-driver/review.md` (package data, generated into the workspace).

Create `source/daily_driver/cli/commands/review.py`:

```python
from daily_driver.cli.commands._claude_session import register_interactive_launcher

def add_parser(subparsers, parents):
    return register_interactive_launcher(
        subparsers,
        cmd_name="review",
        slash_command="/review",
        help_text="Interactive review session",
        session_prefix="review",
        parents=parents,
    )

def run(args):
    return args.func(args)
```

For headless flows, call `launch_headless()` from `_claude_session` with captured stdout.

### Checklist

- [ ] `add_parser` + `run` implemented
- [ ] Registered in `_COMMANDS`
- [ ] Test in `tests/test_cli/`; core logic test in `tests/test_core/`
- [ ] Entry in `docs/commands.md`
- [ ] `[Unreleased]` note in `CHANGELOG.md`
- [ ] (Launcher) shipped slash command in `source/daily_driver/resources/slash_commands/daily-driver/`

## Adding a plugin

A plugin is a feature slice (config + CLI + optional scheduler/doctor/package-data) that core wires through a static registry.
`job_search` is the reference implementation.
No runtime discovery — every plugin is listed explicitly in `plugins.PLUGINS`, and `_validate_registry` `find_spec`-checks every declared hook module at import so a typo'd path fails at startup, not when the command first runs.

### Hello-world: a reminders plugin

A complete, copy-paste plugin that exercises two hooks: a `reminders list` CLI subcommand (`command_module`) that reads its config, and one launchd job (`scheduled_jobs_builder`) fired at a configured `HH:MM`.
The `config_model` and a `doctor` check are shown as clearly-marked OPTIONAL extensions — a plugin needs neither.

Directory layout under `source/daily_driver/plugins/reminders/`:

```
reminders/
  __init__.py     # the PLUGIN descriptor
  cli.py          # command_module: add_parser + run
  config.py       # config_model (OPTIONAL)
  scheduler.py    # scheduled_jobs_builder
  doctor.py       # doctor_checks (OPTIONAL)
```

`__init__.py` — the `PLUGIN` descriptor (the only thing core reads to wire the plugin):

```python
from daily_driver.plugins._base import Plugin
from daily_driver.plugins.reminders.config import RemindersPlugin

PLUGIN = Plugin(
    name="reminders",                    # plugins.reminders config namespace + identifier
    command_name="reminders",            # CLI subcommand verb
    command_module="daily_driver.plugins.reminders.cli",
    command_help="Home-maintenance reminders",
    config_model=RemindersPlugin,        # OPTIONAL: typed plugins.reminders block
    scheduled_jobs_builder="daily_driver.plugins.reminders.scheduler.build_scheduled_jobs",
    launchd_labels=("com.daily-driver.reminders",),  # swept on scheduler uninstall
    doctor_checks="daily_driver.plugins.reminders.doctor.run_checks",  # OPTIONAL
)
```

`cli.py` — the `command_module` hook (`add_parser` + `run`, same shape as a core subcommand):

```python
from __future__ import annotations

import argparse

from daily_driver.cli._common import add_global_flags, resolve_workspace


def add_parser(subparsers, parents):
    parser = subparsers.add_parser("reminders", parents=parents, help="Home reminders")
    nested = parser.add_subparsers(dest="reminders_action", metavar="<action>")
    p_list = nested.add_parser("list", parents=parents, help="List configured reminders")
    add_global_flags(p_list)
    p_list.set_defaults(func=_run_list)
    parser.set_defaults(func=run)
    return parser


def _run_list(args, workspace):
    plugins = workspace.config.plugins
    cfg = plugins.reminders if plugins else None
    for item in (cfg.items if cfg else []):
        print(f"- {item}")
    return 0


def run(args):
    return args.func(args, resolve_workspace(args))
```

`scheduler.py` — the `scheduled_jobs_builder` hook.
`SchedulerContext` carries the scheduler-namespace config and resolved primitives, not the plugin's own config, so the builder rediscovers the workspace from `ctx.workspace_root` to read `plugins.reminders.at`:

```python
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daily_driver.core.scheduler import ScheduledJob, SchedulerContext

LABEL = "com.daily-driver.reminders"


def build_scheduled_jobs(ctx: SchedulerContext) -> list[ScheduledJob]:
    from daily_driver.core.scheduler import ScheduledJob
    from daily_driver.core.workspace import Workspace

    workspace = Workspace.discover_or_fail(override=Path(ctx.workspace_root))
    plugins = workspace.config.plugins
    cfg = plugins.reminders if plugins else None
    if cfg is None or not cfg.at:  # no time configured -> no launchd job
        return []

    stdout, stderr = ctx.log_paths("reminders")
    args = [ctx.dd_bin, "reminders", "list", "--workspace", ctx.workspace_root]
    return [
        ScheduledJob(
            label=LABEL,
            template="checkin.plist.j2",  # reuse core's daily-times template
            program_arguments=args,
            context={
                "label": LABEL,
                "program_arguments": args,
                "times": [ctx.parse_hhmm(cfg.at)],
                "stdout_path": str(stdout),
                "stderr_path": str(stderr),
                "env_path": ctx.env_path,
                "home": ctx.home,
            },
        )
    ]
```

OPTIONAL `config.py` — the `config_model`.
A Pydantic `BaseModel` with `extra="forbid"`; it becomes the typed `plugins.reminders` block (the assembled `PluginsConfig` is `extra="forbid"`, so an unknown key fails validation):

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RemindersPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[str] = Field(default=[], description="Home-maintenance reminders")
    at: str | None = Field(default=None, description="Daily fire time, HH:MM")
```

OPTIONAL `doctor.py` — the `doctor_checks` hook returns `list[CheckResult]`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daily_driver.core.doctor import CheckResult
    from daily_driver.core.workspace import Workspace


def run_checks(workspace: Workspace) -> list[CheckResult]:
    from daily_driver.core.doctor import CheckResult

    plugins = workspace.config.plugins
    cfg = plugins.reminders if plugins else None
    if cfg and cfg.at and not cfg.items:
        return [
            CheckResult(
                name="reminders",
                status="WARNING",
                detail="A fire time is set but no reminders are configured.",
                fix_hint="Add entries under plugins.reminders.items.",
            )
        ]
    return []
```

Register it in the `PLUGINS` tuple in `plugins/__init__.py`:

```python
from daily_driver.plugins.reminders import PLUGIN as _reminders

PLUGINS: tuple[Plugin, ...] = (_job_search, _reminders)
```

Add the config namespace to the workspace `.dd-config.yaml`:

```yaml
plugins:
  reminders:
    items:
      - "Replace furnace filter"
      - "Test smoke detectors"
    at: "09:00"
```

Try it:

```bash
daily-driver reminders list --workspace /path/to/workspace
# - Replace furnace filter
# - Test smoke detectors
```

Core stays import-light: it imports `PLUGINS` lazily inside the functions that iterate it (scheduler, doctor, generate) and imports each plugin's implementation only on dispatch.

### Checklist

- [ ] `plugins/<name>/__init__.py` exposes `PLUGIN`
- [ ] Added to `PLUGINS` in `plugins/__init__.py`
- [ ] `config_model` is a `BaseModel`; tests in `tests/test_plugins/<name>/`
- [ ] `[Unreleased]` entry in `CHANGELOG.md`

### Plugin or tracker category?

Rows alone is a tracker category (no code needed); rows plus a source to scrape or a schedule to run is a plugin.

## Adding a scraper source

This recipe applies to the `job_search` plugin. Each scraper lives in its own module under `source/daily_driver/plugins/job_search/scraper/sources/`. The `SCRAPERS` dict is assembled in that package's `sources/__init__.py`. Shared HTTP / Playwright helpers live in `sources/_http.py`; orchestration (`run_all_scrapers`, dedup, filters) lives in `scraper/runner.py`.

### Pipeline

```
scrape_<name>(ctx: ScrapeContext) → list[dict]
    ↓
run_all_scrapers:
    phase 1 — headless sources in ThreadPoolExecutor (parallel_workers, default 8)
    phase 2 — Playwright sources serial (memory + bot-detection)
    ↓
merge + dedup (URL + company+role key) → filter → enrich_job_details (HTML/JSON-LD)
    → enrich_fit_and_notes (headless claude, optional) → append_jobs_typed (flock-guarded)
```

Phase assignment is code-driven: a source listed in `scraper.runner._PLAYWRIGHT_SOURCES` routes to phase 2. (`SourceToggle` is `extra="forbid"`, so there is no `type:` config knob for this.)

### Scraper interface

```python
def scrape_<name>(ctx: ScrapeContext) -> list[dict]:
    """Read transport/role knobs off ctx.plugin. On transient failures
    (5xx, timeout) log a warning and return an empty or partial list.
    Do not raise — the orchestrator catches exceptions and marks the
    source as failed."""
```

Required keys per returned dict: `company`, `role`, `location`, `url`, `source`, `date_found` (ISO). Optional (empty string if absent): `comp`, `notes`.

Do not normalize in the scraper. `NormalizedJob.from_raw` downstream handles lowercasing country codes, stripping trailing slashes, ISO-coercing dates.

### Register

```python
SCRAPERS: dict[str, Callable[[ScrapeContext], list[dict]]] = {
    ...
    "<name>": scrape_<name>,
}
```

### Fixtures (non-negotiable)

Network calls in tests are forbidden. For each source, commit:

- `tests/fixtures/scraper/<name>/index.html` (or `.json`) — representative listing page
- `tests/fixtures/scraper/<name>/detail-<id>.html` — at least one detail page if the source enriches

Monkeypatch the HTTP client or Playwright context to return fixture bytes; assert on the returned dict shape.

**If you cannot produce a fixture for a page, you cannot write a scraper for it.** Fixtures are how the scraper stays testable when upstream markup changes.

### JobSpy vs native parser

**Use JobSpy** when the source is supported (LinkedIn, Indeed, ZipRecruiter, Glassdoor, Google Jobs) and you don't need custom field extraction. Tradeoff: opaque internals break when upstream changes — Google for Jobs is currently broken upstream (token/JS-gated; returns 0) and is *not* shipped. The library is a developer-facing implementation detail: it surfaces to users only as the two site-named sources `linkedin` and `indeed`. Each registers in `SCRAPERS` as its own site (the `jobs run -S linkedin` / `-S indeed` selectors), both backed by `scrape_jobspy`. The runner (`runner._jobspy_scrape_plan`) always fetches each enabled site in its own `scrape_jobs(site_name=[<site>])` call under its own progress row, so each keeps its own retry and failure isolation. Per-row Source attribution comes from JobSpy's own `site` field via `normalize_jobspy_row`, so each row is labeled `linkedin` / `indeed` individually. In `.dd-config.yaml` they are top-level source keys (`sources.linkedin`, `sources.indeed`); `country` (Indeed's `country_indeed`) lives on the `indeed` toggle only.

**Write a native scraper** when:

- The source has a public API (Greenhouse, Lever, Ashby) — function is `requests.get(api_url)` + field mapping. Prefer this.
- The source serves JSON-LD `JobPosting` in HTML — parse the `<script type="application/ld+json">` tag. More stable than CSS selectors.
- The source needs custom auth or flow that JobSpy cannot express.

Do not write CSS-selector scrapers against rendered pages unless the API and JSON-LD paths are genuinely unavailable. Selectors rot on every frontend refactor.

### Checklist

- [ ] `tests/test_scraper/test_<name>.py` — fixture-backed happy path
- [ ] Skips missing/malformed postings without raising
- [ ] Returns empty or partial list on 5xx / timeout
- [ ] No live network (grep for `requests.get`, `httpx.get`, `sync_playwright` in test output)
- [ ] `SCRAPERS` dict updated
- [ ] Source entry in `docs/commands.md` or `docs/configuration.md` (whichever is nearer to user usage)
- [ ] Playwright / API-key needs noted in `docs/troubleshooting.md` if any
- [ ] `[Unreleased]` entry in `CHANGELOG.md`
