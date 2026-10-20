# Daily Driver

Plan your morning, check in at midday, and review in the evening. You chat with Claude, and Daily Driver remembers the rest and can summarize what you've done over time. All data is saved locally.

## How you use it

You use Daily Driver by chatting with Claude. Claude manages your data for you: it adds and updates items in your list and writes your plans and notes. Each chat starts by reading them, so you do not have to explain your situation again. You rarely need to type any other command.

| When | Start the chat | What happens |
| --- | --- | --- |
| Morning | `daily-driver day-start` | Claude reviews your list, and your calendar if you connect it, and helps you plan the day. |
| Midday | `daily-driver check-in` | Tell Claude what you finished or what is stuck. It updates your list and adjusts the plan. |
| Evening | `daily-driver day-end` | Claude compares your plan to what happened, saves notes, and carries unfinished work to tomorrow. |

Things you can say in a chat:

- "Help me plan today. I have a dentist appointment at 3 pm."
- "Add my open Jira tickets to my list, then help me plan today."
- "I finished the passport renewal. The bank form is blocked until Monday."
- "What should I focus on first?"
- "Summarize what I got done this month."
- "Draft a status update for my manager from my Jira tickets and what I finished this week."

If you close a chat by accident, run `daily-driver resume` to reopen the most recent one.

## Connect your other tools

Each chat runs in Claude Code, so it can use the integrations you already set up there. These are also called Model Context Protocol (MCP) servers. With the Atlassian integration, Claude can read your Jira tickets. With Google integrations, it can work with Google Calendar, Drive, and Gmail. Set them up once in Claude Code, and every chat can use them.

## What you need

- **A Mac.** Daily Driver is built for Macs with Apple silicon. Intel Macs probably work but are untested. Windows and Linux are not supported.
- **Homebrew**, an app installer for macOS. See [brew.sh](https://brew.sh).
- **pipx**, a tool that installs Python programs. It installs Daily Driver in its own folder, so Daily Driver does not change other Python programs.
- **Claude Code**, installed and signed in. Every chat runs through it.
- **Optional: icalBuddy**, a small tool that lets Daily Driver read your calendar. Reading the calendar can fail in the built-in terminal of Visual Studio Code (VS Code) on recent versions of macOS. See [Troubleshooting](docs/troubleshooting.md#calendar-events-are-missing-in-vs-code).

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

The last command prints the version number if the install worked. The [install guide](docs/install.md) covers upgrades and optional tools.

## Get started

1. Create your workspace. This is the folder where Daily Driver keeps your data:

   ```bash
   daily-driver init ~/daily-driver-workspace
   cd ~/daily-driver-workspace
   ```

2. Check the setup. Each line shows OK or what to fix:

   ```bash
   daily-driver doctor
   ```

3. Open `context.md` in your workspace and describe your work, goals, and routines. Claude reads it when it plans your day.

4. Start your first chat. Tell Claude what you work on and what is due, and ask it to add your tasks to your list:

   ```bash
   daily-driver day-start
   ```

Run the commands from inside your workspace folder, or add `--workspace ~/daily-driver-workspace` to run them from anywhere. The [daily guide](docs/usage.md) walks through a full day.

## Optional extras

- **Reminders.** Run `daily-driver scheduler install` to get a check-in reminder at 11:00 and 15:00. Click the reminder to open the chat. Clicking needs one optional tool; see the [install guide](docs/install.md).
- **Job search.** Daily Driver can search the job sites you choose, remove duplicates, and score how well each job fits you. Turn it on with `--job-search` when you create a workspace. The [guide](docs/usage.md#jobs) also shows how to add it to an existing workspace.
- **Local AI.** Run some AI tasks on your own computer with Ollama instead of Claude. See [Local AI setup](docs/ollama-setup.md).

## Commands you can run yourself

Claude runs these for you in a chat. You can also run them yourself:

| Command | What it does |
| --- | --- |
| `daily-driver status` | Shows totals, items with no update in 14 days, and your activity over the last 7 days. |
| `daily-driver tracker list` | Shows your list. |
| `daily-driver tracker add --category task --title "Call the dentist"` | Adds an item to your list. |
| `daily-driver summary --range 7d` | Summarizes the last 7 days and copies the summary to your clipboard. |
| `daily-driver focus on` | Pauses check-in reminders for 25 minutes. Add `--for 2h` for longer. |
| `daily-driver voice-update --from <file>` | Learns your writing style from samples, so drafts sound like you. |

## Your workspace

Your workspace is a normal folder on your Mac. Daily Driver has no server and does not send usage data anywhere. These are the files you will use most:

| File | What it is |
| --- | --- |
| `context.md` | About you: your work, goals, and routines. Claude reads it. |
| `voice-profile.md` | Your writing style. Daily Driver builds it from your writing samples. |
| `.dd-config.yaml` | Your settings. |
| `tracker.yaml` | Your list. Claude keeps it up to date, so you rarely need to open it. |
| Folders named by year and month | Your daily plans and notes. Claude writes them. |

Daily Driver manages the `.claude` and `.daily-driver` folders. Leave them alone unless a guide tells you otherwise. [How it works](docs/concepts.md) has details.

## Learn more

| Guide | What you will find |
| --- | --- |
| [Install](docs/install.md) | Requirements, upgrades, and optional tools |
| [Quick start](docs/quick-start.md) | A short checklist to set up your workspace |
| [Daily guide](docs/usage.md) | A full walkthrough of a day with Daily Driver |
| [How it works](docs/concepts.md) | The ideas behind the workspace, tracker, and AI chats |
| [Commands](docs/commands.md) | Every command and option, plus a [command tree](docs/cli-tree.md) |
| [Settings](docs/configuration.md) | Every setting in your settings file |
| [Local AI setup](docs/ollama-setup.md) | Run some AI tasks on your own computer |
| [Troubleshooting](docs/troubleshooting.md) | Fixes for common problems |

**Developers:** see the [developer docs](docs/dev/README.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
