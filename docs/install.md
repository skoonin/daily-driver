# Install

macOS arm64 only in v0.1.0. Intel Mac likely works but is untested. Linux is on the roadmap.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | 3.12 recommended |
| `claude` CLI on `$PATH` | Required for `day-start`, `day-end`, `check-in`, `summary`, `voice-update`. See [Anthropic install docs](https://docs.anthropic.com/en/docs/claude-code/install). |
| macOS CLI tools | `pbcopy`, `osascript`, `launchctl` — ship with macOS |
| Playwright browsers | Optional; needed only for Wellfound and Apple careers scraping |

Do not install into the system Python. Both Homebrew Python and pyenv work. Homebrew replaces Python on `brew upgrade`; if the minor version shifts, your venv breaks. pyenv pins the interpreter independently.

## Install

```bash
pip install git+https://github.com/skoonin/daily-driver.git
daily-driver --version
```

## Development install

```bash
git clone https://github.com/skoonin/daily-driver.git
cd daily-driver
make setup         # creates .venv, installs .[dev], installs pre-commit hooks
make test
```

## Playwright (optional)

```bash
pip install playwright
playwright install chromium
```

The scraper logs a warning and skips Playwright-only sources when unavailable. All other sources (Greenhouse, RemoteOK, WWR, HN, JobSpy) keep working.

## Upgrade

```bash
pip install --upgrade git+https://github.com/skoonin/daily-driver.git
daily-driver --version
```

On the next command invocation, Daily Driver detects version-stamp drift and re-materializes `.claude/commands/daily-driver/`, `.claude/agents/daily-driver/`, and `.claude/settings.local.json`. Anything outside those three paths is untouched.

### Breaking config changes

`.dd-config.yaml` is validated with Pydantic (`extra="forbid"`). Schema-breaking releases surface a validation error naming the offending key. No automatic migrations in v0.1.0 — read `CHANGELOG.md`, edit the config, run `daily-driver doctor`.

### Force re-materialize

```bash
daily-driver doctor --reset
```

Wipes the managed `.claude/daily-driver/` subdirs, recopies from the installed package, and rewrites the version stamp.

### Scheduler after upgrade

`install-scheduler` is idempotent — re-run it after an upgrade to pick up template or binary-path changes. It unloads, rewrites, reloads.

### Downgrade

```bash
pip install 'git+https://github.com/skoonin/daily-driver.git@v0.0.X'
daily-driver doctor --reset
```

Version stamps compare by equality, not ordering; downgrade and upgrade are handled identically.
