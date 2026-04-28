# Contributing to Daily Driver

Daily Driver is v0.1.0 alpha; internal surface is churning. Dev workflow below is stable. All docs live under `docs/` — see [docs/developer.md](docs/developer.md) for architecture, [docs/extending.md](docs/extending.md) for subcommand/scraper recipes, and [docs/releasing.md](docs/releasing.md) for releases.

## Setup

```bash
git clone https://github.com/skoonin/daily-driver.git
cd daily-driver
make setup
make test
```

The scraper needs Playwright browsers for Wellfound and Apple sources:

```bash
playwright install chromium
```

## Tests and markers

Markers are declared in `pyproject.toml`. Skip with `-m "not <marker>"`.

| Marker | Purpose |
|--------|---------|
| `unit` | Pure unit tests, no I/O |
| `integration` | Multi-component interaction tests |
| `e2e` | Full CLI workflow tests |
| `platform_macos` | Requires macOS (flock, launchctl, icalBuddy) |
| `needs_claude` | Requires `claude` CLI on PATH |
| `needs_icalbuddy` | Requires `icalBuddy` on PATH |
| `needs_jobspy` | Requires `python-jobspy` |
| `needs_browser` | Requires a Playwright browser |
| `slow` | Long-running |

**Spawn-worker PYTHONPATH:** the root `conftest.py` exports `PYTHONPATH` so multiprocessing spawn workers (used by some gather tests) can resolve the `tests.*` package. Do not remove this — it is a known constraint of the spawn start method. A cryptic `ModuleNotFoundError: No module named 'tests'` in a subprocess is the symptom.

## Style and tooling

All configured in `pyproject.toml`, enforced by pre-commit:

| Tool | Config |
|------|--------|
| black | line-length 88, target-version py311 |
| isort | profile black, line-length 88 |
| flake8 | max-line-length 160, ignores E203/E501/W503 |
| mypy | strict, `source/` on path, pydantic plugin enabled |

Public functions need type hints. Public APIs need Google-style docstrings (document parameters, returns, and raised exceptions). `TODO` comments require a ticket reference (e.g., `TODO(DD-123):`); bare TODOs get rejected in review.

## Commits

- One logical change per commit; 2-line messages.
- No `--no-verify`. If a hook fails, fix the underlying issue.
- Imperative voice. Match the tone of recent `git log --oneline`.
- No AI attribution.

## Working documents

Plans, notes, analysis files live under `.claude/` at the repo root (gitignored). Prefix planning files with `plan-` (e.g., `.claude/plans/plan-auth-refactor.md`).

## Questions and bugs

<https://github.com/skoonin/daily-driver/issues>. For Claude-orchestration questions, include the command, workspace layout, and redacted `-v` output.
