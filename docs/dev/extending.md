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

A plugin is a feature slice (config + CLI + optional scheduler/doctor/package-data) that core wires through a static registry. `job_search` is the reference implementation. No runtime discovery — every plugin is listed explicitly.

1. Create `source/daily_driver/plugins/<name>/` with an `__init__.py` exposing a module-level `PLUGIN`:

```python
from daily_driver.plugins._base import Plugin
from daily_driver.plugins.<name>.config import MyPluginConfig

PLUGIN = Plugin(
    name="<name>",                       # plugins.<name> config namespace + identifier
    command_name="<verb>",               # CLI subcommand
    command_module="daily_driver.plugins.<name>.cli",
    command_help="One-line --help text",
    config_model=MyPluginConfig,         # BaseModel for plugins.<name>
    # Optional contributions:
    # scheduled_jobs_builder="daily_driver.plugins.<name>.scheduler.build_scheduled_jobs",
    # doctor_checks="daily_driver.plugins.<name>.doctor.run_checks",
    # package_data_dirs=(PackageDataDir("daily_driver.plugins.<name>.commands", "commands/<name>"),),
)
```

2. Add it to the `PLUGINS` tuple in `plugins/__init__.py`. `_validate_registry` runs at import and `find_spec`-checks every declared module, so a typo'd hook path fails at startup.

3. Implement only what `PLUGIN` declares. `command_module` exposes `add_parser` / `run` (see "Adding a subcommand"); `config_model` is a Pydantic `BaseModel` (it becomes `plugins.<name>` and is `extra="forbid"` via the assembled `PluginsConfig`); `scheduled_jobs_builder` returns `list[ScheduledJob]`; `doctor_checks` returns `list[CheckResult]`; `package_data_dirs` declares `.md` dirs copied into the workspace `.claude/` tree by `core.generate`.

Core stays import-light: it imports `PLUGINS` lazily inside the functions that iterate it (scheduler, doctor, generate) and imports each plugin's implementation only on dispatch.

### Checklist

- [ ] `plugins/<name>/__init__.py` exposes `PLUGIN`
- [ ] Added to `PLUGINS` in `plugins/__init__.py`
- [ ] `config_model` is a `BaseModel`; tests in `tests/test_plugins/<name>/`
- [ ] `[Unreleased]` entry in `CHANGELOG.md`

## Adding a scraper source

This recipe applies to the `job_search` plugin. Each scraper lives in its own module under `source/daily_driver/plugins/job_search/scraper/sources/`. The `SCRAPERS` dict and typed `SOURCE_REGISTRY` are assembled in that package's `sources/__init__.py`. Shared HTTP / Playwright helpers live in `sources/_http.py`; orchestration (`run_all_scrapers`, dedup, filters) lives in `scraper/runner.py`.

### Pipeline

```
scrape_<name>(config) → list[dict]
    ↓
run_all_scrapers:
    phase 1 — headless sources in ThreadPoolExecutor (parallel_workers, default 4)
    phase 2 — Playwright sources serial (memory + bot-detection)
    ↓
merge + dedup (URL + company+role key) → filter → enrich_job_details (HTML/JSON-LD)
    → enrich_fit_and_notes (headless claude, optional) → append_jobs (flock-guarded)
```

Phase assignment is config-driven: `type: playwright` on a source routes it to phase 2.

### Scraper interface

```python
def scrape_<name>(config: dict) -> list[dict]:
    """On transient failures (5xx, timeout) log a warning and return
    an empty or partial list. Do not raise — the orchestrator
    catches exceptions and marks the source as failed."""
```

Required keys per returned dict: `company`, `role`, `location`, `url`, `source`, `date_found` (ISO). Optional (empty string if absent): `comp`, `notes`, `gd_rating`, `product_purpose`.

Do not normalize in the scraper. `normalize_job()` downstream handles lowercasing country codes, stripping trailing slashes, ISO-coercing dates.

### Register

```python
SCRAPERS: dict[str, Callable[[dict], list[dict]]] = {
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

**Use JobSpy** when the source is supported (LinkedIn, Indeed, ZipRecruiter, Glassdoor, Google Jobs) and you don't need custom field extraction. Tradeoff: opaque internals break when upstream changes.

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
