# How it works

This page explains the parts of Daily Driver and how they fit together. Read it once. Then use the [daily guide](usage.md) for the daily routine and [Commands](commands.md) for every option.

## What it does

You use Daily Driver by chatting with Claude, an AI assistant. Claude manages your data for you: it adds and updates items in your list (the tracker) and writes your plans and notes. Each chat starts by reading them, so you do not have to explain your situation again. You rarely need to type any other command.

You start a chat by typing `daily-driver day-start`, `daily-driver check-in`, or `daily-driver day-end` in the Terminal app. Daily Driver saves your data as files on your computer. All your files are in one folder that you create once. This folder is called a workspace.

## Workspace layout

```
workspace/                 your data
├── .dd-config.yaml        your settings (categories, schedule, job search)
├── context.md             your background and tasks that repeat; Claude reads it
├── voice-profile.md       how you write; Claude uses it to draft in your style
├── tracker.yaml           your list (the tracker): anything you follow up on
├── jobs.csv               saved job postings (only when job search is on)
├── 2026/05/               plan and notes files, such as 2026-05-11-plan.md (created on demand)
└── .claude/, .daily-driver/  Daily Driver manages these. Do not edit them.
```

The workspace is an ordinary folder. Everything is saved in files that you can read, edit, and back up. Daily Driver has no server and does not send usage data anywhere.

## Features

| Feature | What it does | Commands |
| --- | --- | --- |
| **Daily sessions** | Chats with Claude for the morning, midday, and evening | `day-start`, `check-in`, `day-end`, `resume` |
| **Your list (the tracker)** | Your tasks, errands, contacts, and anything else you follow up on. Claude keeps it up to date. | `tracker` |
| **Workspace** | The folder that holds your data. Most people use one. | `init`, `doctor`, `paths` |
| **Status** | One screen with your totals, items with no recent update, and recent activity | `status` |
| **Focus** | Pauses scheduled check-in reminders while you work without interruptions | `focus` |
| **Calendar and Git** | Reads your macOS Calendar events and Git commits, so Claude knows about your day | `gather` |
| **Job search** (optional, `init --job-search`) | Finds job postings on the sites you choose, saves them in `jobs.csv`, and rates how well each one fits you | `jobs` |
| **Summary** | Writes a summary of any period and copies it to your clipboard | `summary` |
| **Voice** | Learns how you write from your own writing samples | `voice-update` |
| **Scheduler** | Starts reminders at set times (macOS) | `scheduler` |
| **Help** | Shows lists of valid values | `help` |

## Good to know

- **Claude is the front end. Daily Driver keeps your data.** Claude changes your list by running `daily-driver` commands. It writes your plan and notes files itself. A new chat starts without memory of earlier chats, so each chat begins by reading your files.
- **Run commands inside your workspace folder or one of its subfolders.** To run them from anywhere, add `--workspace PATH` (or `-w PATH`) to a command.
- **Daily Driver manages some files in `.claude/`.** It updates them after an upgrade when you run `daily-driver doctor --fix`. Put your own Claude commands and agents in `.claude/commands/user/` and `.claude/agents/user/`. Daily Driver never changes those folders. See [`.claude/` ownership](configuration.md#claude-ownership).
- **Settings do not change on their own.** If an upgrade removes or renames a setting, commands stop and name it. `CHANGELOG.md` says what to change.
