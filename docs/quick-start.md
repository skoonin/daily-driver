# Quick start

Follow these steps to set up your workspace and start your first chat with Claude. Your workspace is the folder where Daily Driver keeps your data. Type the commands in the Terminal app. Then read the [daily guide](usage.md) for a full day with Daily Driver.

## 1. Create your workspace

```bash
daily-driver init ~/daily-driver-workspace
cd ~/daily-driver-workspace
```

`init` creates these files and folders:

| Name | What it holds | Do you edit it? |
|---|---|---|
| `.dd-config.yaml` | Your settings | Yes |
| `context.md` | Your background and tasks that repeat. Claude reads it. | Yes |
| `voice-profile.md` | How you write, so Claude can draft in your style | Yes |
| `.claude/` | Claude commands, agents, and settings | Only `commands/user/` and `agents/user/` |
| `README.md`, `.gitignore`, `.daily-driver/` | A command list, a Git ignore list, and Daily Driver records | Usually not |

Job search is off until you switch it on. To switch it on in a new workspace, add `--job-search`: `daily-driver init --job-search ~/daily-driver-workspace`. In an existing workspace, this flag changes nothing unless you also pass `--force`. `init` then prints the steps to follow. See [Jobs](usage.md#jobs).

You can run `init` again safely. It adds files that are missing and keeps the others. It never changes `context.md`, `voice-profile.md`, or `.gitignore` after you create them.

To reset your settings, run `daily-driver init --force`. This replaces `.dd-config.yaml` with the default file, so copy any settings you want to keep first. It also replaces the Daily Driver files in `.claude/`, and your edits to them are lost. See [`.claude/` ownership](configuration.md#claude-ownership).

## 2. Check the setup

```bash
daily-driver doctor
```

`doctor` checks that your workspace and tools work. Each row of its table is OK, WARNING, or ERROR. A WARNING means you should check something. An ERROR means something is broken.

| Command | What it does |
|---|---|
| `daily-driver doctor --fix` | Repairs problems and restores Daily Driver files that you deleted. Keeps your edits to those files. |
| `daily-driver doctor --reset` | Replaces the Daily Driver files in `.claude/` with fresh copies. Your edits to them are lost. |

Use `--fix` or `--reset`, not both.

## 3. Describe yourself

Open `context.md` in your workspace. Describe your work, your goals, and your routines. Add anything a new coworker should know. List tasks that repeat under `## Recurring Tasks`, one per line. Claude reads this file when it plans your day.

`voice-profile.md` can wait until you have writing samples. See [Voice updates](usage.md#voice-updates).

## 4. Start your first chat

```bash
daily-driver day-start
```

This opens a chat with Claude that helps you plan your day. Tell Claude what you work on and what is due. Ask it to add your tasks to your list (the tracker). Claude saves your list, plan, and notes in your workspace.

Run the commands from inside your workspace folder, or add `--workspace ~/daily-driver-workspace` to run them from anywhere. For ideas on what to say, see [Daily sessions](usage.md#daily-sessions).

## 5. See your list (optional)

Claude keeps your list up to date. To look at it yourself, run:

```bash
daily-driver tracker list
daily-driver status
```

`status` shows:

- how many items you have
- items that are not finished and have no update for more than 14 days
- items that changed in the last 7 days

To add an item yourself, run `daily-driver tracker add -c task -T "renew passport"`. `-c` sets the category and `-T` sets the title.

## 6. Reminders (optional, macOS only)

```bash
daily-driver scheduler install
```

This sets up check-in reminders at 11:00 and 15:00. It uses launchd, the macOS tool that runs tasks at set times. Click a reminder to open the chat. Clicking needs the optional `terminal-notifier` tool. See the [install guide](install.md).

For other tasks and times, see [Scheduler](usage.md#scheduler-macos). `daily-driver scheduler status` shows what is installed. `daily-driver scheduler uninstall` removes the reminders.

## If a command fails

Run it again with `-v` to see more detail. Use `-vv` for the most detail. Use `-q` to show errors only.

```bash
daily-driver doctor -v
```

For fixes to common problems, see [Troubleshooting](troubleshooting.md).

## Next

- [Daily guide](usage.md): a full day with Daily Driver.
- [Commands](commands.md): every command and option.
- [Settings](configuration.md): the settings in `.dd-config.yaml`.
