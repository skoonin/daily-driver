# Install

macOS arm64 only. Intel Mac likely works but is untested. Linux is on the roadmap.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | 3.12 recommended |
| `claude` CLI on `$PATH` | Required for `day-start`, `day-end`, `check-in`, `summary`, `voice-update`. See [Anthropic install docs](https://code.claude.com/docs/en/install). |
| macOS CLI tools | `pbcopy`, `osascript`, `launchctl` — ship with macOS |
| `icalBuddy` | Optional; reads macOS Calendar for `gather calendar` and `day-start`. Install with `brew install ical-buddy`, then allow your terminal to read Calendar. |
| `terminal-notifier` | Optional; makes scheduled check-in, day-start, and day-end notifications clickable. Install with `brew install terminal-notifier`. |
| Playwright browser binaries | Optional; job search only, and only for the Apple careers scraper |

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
make setup-dev     # creates .venv, installs .[dev], and installs pre-commit hooks
make test
```

`make setup` does the same without the pre-commit hooks.

## Playwright browser install (optional)

When a Playwright source (Apple) is enabled, `daily-driver doctor` flags the missing browser as a WARNING on macOS. Lead with:

```bash
daily-driver doctor --fix
```

If you need to install manually instead, use the Python interpreter daily-driver runs under rather than a bare `playwright` CLI, which may resolve to the wrong interpreter and browser cache — `doctor`'s own warning prints the exact command, anchored to that interpreter:

```bash
<path-to-python> -m playwright install firefox
```

All other sources (Greenhouse, Ashby, Lever, Workable, Workday, RemoteOK, WWR, HN Who's Hiring, HN YC-funded jobs, LinkedIn/Indeed via JobSpy) keep working without Playwright.

## Next

- [usage.md](usage.md) — first-day walkthrough.
- [quick-start.md](quick-start.md) — minimal scaffold checklist.

## Upgrade

```bash
pip install --upgrade git+https://github.com/skoonin/daily-driver.git
daily-driver --version
```

After an upgrade, `daily-driver doctor` reports `Workspace drift` as a WARNING. Run `daily-driver doctor --fix` to regenerate the managed files:

- Kept: your edits to shipped files.
- Deleted: `.md` files you added to `.claude/commands/daily-driver/` or `.claude/agents/daily-driver/`, and `.sh` files you added to `.claude/hooks/`.
- Replaced: the `permissions`, `hooks`, and `metadata` blocks in `.claude/settings.local.json`, including allow rules Claude Code saved.

See [`.claude/` ownership](configuration.md#claude-ownership) for what `init` and `doctor` keep, overwrite, and delete.

### Breaking config changes

`.dd-config.yaml` is validated with Pydantic (`extra="forbid"`). Schema-breaking releases surface a validation error naming the offending key. No automatic migrations — read `CHANGELOG.md`, edit the config, run `daily-driver doctor`.

### Force regenerate

```bash
daily-driver doctor --reset
```

`doctor --reset` and `init --force` wipe the managed directories (`.claude/commands/daily-driver/`, `.claude/agents/daily-driver/`, and `.claude/hooks/`) and recopy them from the package, so your edits and every file you added there are lost. `init --force` also resets `.dd-config.yaml`.

### Scheduler after upgrade

`scheduler install` is idempotent — re-run it after an upgrade to pick up template or binary-path changes. It unloads, rewrites, reloads.

### Downgrade

```bash
pip install 'git+https://github.com/skoonin/daily-driver.git@vX.Y.Z'
daily-driver doctor --fix
```

Version stamps compare by equality, not ordering; downgrade and upgrade are handled identically.
