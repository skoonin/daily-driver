# Install

Daily Driver runs on Macs with Apple silicon (M1 or newer). Intel Macs probably work but are not tested. Windows and Linux are not supported.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| pipx and Python 3.12 | pipx installs Daily Driver in its own folder with Python 3.12, the version Daily Driver is tested on. It does not change other Python programs. See [Install](#install). |
| Claude Code (the `claude` command) | Needed for `day-start`, `day-end`, `check-in`, `summary`, and `voice-update`. Install it and sign in. See the [Anthropic install guide](https://code.claude.com/docs/en/install). |
| `icalBuddy` | Optional. Lets Daily Driver read your macOS Calendar. See [Calendar access](#calendar-access-optional). |
| `terminal-notifier` | Optional. Makes the scheduled reminders clickable. Install it with `brew install terminal-notifier`. |
| Playwright browser | Optional. Only the Apple source of job search needs it. See [Playwright browser install](#playwright-browser-install-optional). |

The `brew` commands need Homebrew, an app installer for macOS. See [brew.sh](https://brew.sh).

A `brew upgrade` can replace the Python that pipx uses. If Daily Driver stops working after that, run `pipx reinstall --python python3.12 daily-driver`.

## Install

```bash
brew install pipx python@3.12
pipx ensurepath
```

Open a new terminal window, then install Daily Driver:

```bash
pipx install --python python3.12 git+https://github.com/skoonin/daily-driver.git
daily-driver --version
```

The last command prints the version number if the install worked.

## Calendar access (optional)

Daily Driver can read your macOS Calendar, so Claude knows about your meetings when it plans your day. Follow these steps once:

1. Install icalBuddy with `brew install ical-buddy`. To check, run `which icalBuddy`.
2. Open **System Settings > Privacy & Security > Calendars**.
3. Turn on the app you type `daily-driver` in, such as Terminal or iTerm2.
4. If the first run still fails, restart that app.

You can also choose which calendars to read. Without this step, icalBuddy reads all your calendars, including subscribed calendars that add noise. Run `icalBuddy calendars` to list your calendars. Then run these two commands. Replace `Work,Personal` with the calendar names you want, separated by commas:

```bash
/usr/libexec/PlistBuddy -c "Add :constantArguments dict" ~/.icalBuddyConfig.plist
/usr/libexec/PlistBuddy -c "Add :constantArguments:includeCals string Work,Personal" ~/.icalBuddyConfig.plist
```

For all icalBuddy settings, run `man icalBuddyConfig`.

Known issue: on recent versions of macOS, icalBuddy can fail in the Visual Studio Code (VS Code) terminal. The cause is a macOS permission problem that involves VS Code. A tested fix is not available yet. See [Calendar events are missing in VS Code](troubleshooting.md#calendar-events-are-missing-in-vs-code).

If calendar events do not appear, run this command to see the error from icalBuddy. Replace both dates with real dates in the form `2026-11-02`:

```bash
icalBuddy -iep title,datetime,location -nc -nrd -b '' -df '%Y-%m-%d' -tf '%H:%M' eventsFrom:YYYY-MM-DD to:YYYY-MM-DD
```

## Playwright browser install (optional)

Only the Apple source of job search needs a Playwright browser. If the Apple source is on and the browser is missing, `daily-driver doctor` shows a WARNING. To install the browser, run:

```bash
daily-driver doctor --fix
```

If that does not work, run the command that the `doctor` warning prints. It uses the correct Python for your install. All other job sources work without this browser.

## Upgrade

```bash
pipx install --force git+https://github.com/skoonin/daily-driver.git
daily-driver --version
```

After an upgrade, `daily-driver doctor` shows a WARNING named `Workspace drift`. This means some Daily Driver files in your workspace are out of date. Run `daily-driver doctor --fix` to update them:

- Kept: your edits to the Daily Driver files that came with the program.
- Deleted: `.md` files you added to `.claude/commands/daily-driver/` or `.claude/agents/daily-driver/`, and `.sh` files you added to `.claude/hooks/`. Put your own files in `.claude/commands/user/` and `.claude/agents/user/`.
- Replaced: the `permissions`, `hooks`, and `metadata` sections of `.claude/settings.local.json`. This includes permission rules that Claude Code saved when you chose not to be asked again.

For the full list of what `init` and `doctor` keep, replace, and delete, see [`.claude/` ownership](configuration.md#claude-ownership).

### Removed or renamed settings

Some releases remove or rename a setting. If `.dd-config.yaml` uses a removed setting, Daily Driver stops and names the setting. Read `CHANGELOG.md` to find what to change. Edit `.dd-config.yaml`, then run `daily-driver doctor`. Daily Driver does not edit your settings for you.

### Reset Daily Driver files

```bash
daily-driver doctor --reset
```

This replaces every Daily Driver file in `.claude/` with a fresh copy. These folders are `.claude/commands/daily-driver/`, `.claude/agents/daily-driver/`, and `.claude/hooks/`. Your edits to these files, and files you added to these folders, are lost. `daily-driver init --force` does the same and also resets `.dd-config.yaml`.

### Scheduler after an upgrade

After an upgrade, run `daily-driver scheduler install` again. This updates your scheduled reminders to match the new version.

### Downgrade

```bash
pipx install --force 'git+https://github.com/skoonin/daily-driver.git@vX.Y.Z'
daily-driver doctor --fix
```

Replace `vX.Y.Z` with the release you want, for example `v1.2.0`.

## Next

- [Quick start](quick-start.md): set up your workspace and start your first chat.
- [Daily guide](usage.md): a full day with Daily Driver.
