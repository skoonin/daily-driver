# Daily guide

How to use Daily Driver each day. You chat with Claude, and Claude keeps your list (the tracker), plans, and notes. This guide covers the daily chats, your list, focus mode, job search, summaries, your writing voice, and the scheduler.

- Set up your workspace first: [Quick start](quick-start.md)
- What the parts are and how they fit together: [How it works](concepts.md)

## Daily sessions

After the [quick start](quick-start.md), use three chats with Claude each day. Type each command in the Terminal app, inside your workspace folder. Claude manages your data for you: it adds and updates items in your list and writes your plans and notes.

| When | Start the chat | What happens |
| --- | --- | --- |
| Morning | `daily-driver day-start` | Claude reviews your list, and your calendar if you connect it, and helps you plan the day. |
| Midday | `daily-driver check-in` | Tell Claude what you finished or what is stuck. It updates your list and adjusts the plan. |
| Evening | `daily-driver day-end` | Claude compares your plan to what happened, saves notes, and carries unfinished work to tomorrow. |

To connect your calendar, see [Calendar access](install.md#calendar-access-optional).

Each chat saves files in your workspace folder:

- `day-start` saves the plan in a file such as `2026/11/2026-11-02-plan.md`. Run `daily-driver paths daily-plan` to print the path.
- `day-end` saves the notes in a file such as `2026/11/2026-11-02-notes.md`. Run `daily-driver paths daily-notes` to print the path.
- Your list is in `tracker.yaml`. Claude updates it during the chat.

To save these files in another folder, change `daily_driver.output_dir` in `.dd-config.yaml`.

Things you can say in a chat:

- "Help me plan today. I have a dentist appointment at 3 pm."
- "Add my open Jira tickets to my list, then help me plan today."
- "I finished the passport renewal. The bank form is blocked until Monday."
- "What should I focus on first?"
- "Summarize what I got done this month."
- "Draft a status update for my manager from my Jira tickets and what I finished this week."

Options:

- `check-in` starts a new chat each time. To continue your most recent chat instead, set `claude.resume_check_in: true` in `.dd-config.yaml`. With that setting on, add `--no-resume` to start a new chat.
- If you close a chat by mistake, run `daily-driver resume` to reopen the most recent one.
- Each command runs a Claude command of the same name, such as `/daily-driver:day-start`. For more options, see [Commands](commands.md#interactive-claude-launchers).

See also: [Connect your other tools](../README.md#connect-your-other-tools).

## Your list (the tracker)

Your list holds the things you follow up on. Claude keeps it up to date in your chats. You can also work with it yourself. Each item has a category (`task`, `errand`, `contact`, `ticket`, or any name you choose), a title, a status, and an identifier (ID). An ID is the category, a dash, and a number, such as `errand-001`.

To require fields for a category, or to show it in `daily-driver help categories`, list it under `tracker.categories` in `.dd-config.yaml`. See [Settings](configuration.md#custom-tracker-categories).

Claude runs these commands for you in a chat. You can also run them yourself:

| Command | What it does |
|---|---|
| `tracker add -c CATEGORY -T TITLE` | Adds an item |
| `tracker list` | Shows your items. Filter with `-c` (category), `-s` (status), `-t` (tag), or `--since` (changed since a date). |
| `tracker show ID` | Shows one item in full |
| `tracker update ID` | Changes an item. `-s` sets the status, `-N` adds a note, `-t` replaces the tags. |
| `tracker delete ID` | Deletes one item |
| `tracker prune` | Deletes many items at once. You must give at least one filter. Add `-n` to preview. |
| `tracker follow-ups` | Shows unfinished items that have a next action. `--overdue` shows only those past their due date. |
| `tracker stats` | Shows counts by category and status |

```bash
daily-driver tracker add -c errand -T "Book dentist" -s open -d 2026-11-15 --next-action "call the office" --extra phone=555-0100
daily-driver tracker update errand-001 -s blocked -N "waiting on a callback"
daily-driver tracker prune -c errand -s done --older-than month -n   # preview first; nothing is deleted
```

In the first command, `-s` sets the status, `-d` sets the due date, `--next-action` records the next step, and `--extra` adds your own field. In the second command, `-N` adds a note.

### Statuses

Each item has a status. These are the suggested statuses:

| Status | Meaning |
|---|---|
| `open` | Not started |
| `in-progress` | You are working on it |
| `blocked` | You are waiting on something |
| `done` | Finished |
| `ruled-out` | You decided not to do it |

You can use other statuses. Daily Driver shows a one-line notice when you use a new one. To turn the notice off, set `tracker.warn_unknown_status: false`. Run `daily-driver help statuses` to see the suggested statuses and the ones you use. Items from `jobs promote` use these statuses: `found`, `pending`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, and `closed`.

### Date words

Some options accept a date word, such as `--since`, `--older-than`, and `summary --range`. For tracker commands, `--since` keeps items that changed on or after the date. `--older-than` selects items that last changed before the date.

| You type | It means |
|---|---|
| `today`, `yesterday`, `tomorrow` | That day |
| `week` | Monday of this week |
| `month` | The first day of this month |
| `quarter` | The first day of this quarter |
| `year` | 1 January of this year |
| `7d`, `2w`, `3m`, `1y` | That many days, weeks, months, or years ago |
| `2026-11-15` | That exact date |

`daily-driver help dates` shows the date words and the options that accept them.

## Focus mode

Focus mode pauses scheduled check-in reminders while you work without interruptions. A check-in that you start yourself still runs.

```bash
daily-driver focus on --for 90m --reason "write the report"
daily-driver focus status
daily-driver focus off
```

`--for` sets how long focus mode lasts: `30m`, `2h`, `1h30m`, or a number of minutes. The default is 25 minutes. You can change it with `focus.default_duration` in `.dd-config.yaml`. If focus mode seems stuck, see [Troubleshooting](troubleshooting.md#focus-mode-stuck-on).

## Jobs

Job search is optional. It looks for new job postings on the sites you choose and saves them in a spreadsheet file named `jobs.csv`. It skips postings you already have. Then an AI model fills in missing details, such as how well each job fits you, notes, and pay.

### Enable it

Job search is off by default. Until you switch it on, every `jobs` command prints a message and stops.

1. For a new workspace, run `daily-driver init --job-search PATH`.
2. For an existing workspace, add `plugins: {job_search: {}}` to `.dd-config.yaml`. Then run `daily-driver doctor --fix`. This adds job search to your Claude commands.
3. Open `context.md`. Add the places you want to work and the roles you want. Daily Driver does not add these for you.
4. If you use the scheduler, run `daily-driver scheduler install`. This adds the job search tasks.
5. Add `scraper.enabled: true` and your sources to `.dd-config.yaml`, as in the example below. Do not add a second `plugins:` line. Add these lines under the one that exists.

```yaml
plugins:
  job_search:
    persona: "Experienced project coordinator, open to remote roles"
    roles: [project coordinator, project manager]
    domain_keywords: [project, program, operations]
    seniority_keywords: [senior, lead]
    locations:
      home_city: Vancouver, BC
      remote: true
      # Country code. [] means the whole country. A list of cities means only those cities.
      countries: {US: [], CA: [Vancouver, Victoria]}
    scraper:
      enabled: true
    sources:
      greenhouse:
        enabled: true
        greenhouse_boards: [company-a, company-b, company-c]
      hn_jobs: {}
      hn_who_is_hiring: {}
```

`roles`, `domain_keywords`, and `seniority_keywords` decide which job titles are kept. If you leave `domain_keywords` or `seniority_keywords` empty, job search uses built-in keywords for infrastructure jobs. For every job search setting, see [Settings](configuration.md#pluginsjob_search).

To switch job search off, set `enabled: false` under `plugins.job_search` and run `daily-driver doctor --fix`. If you use the scheduler, run `daily-driver scheduler install` to remove the job search tasks. Your settings stay.

### Sources

| Source | What it searches |
|---|---|
| `remoteok` | Remote jobs on RemoteOK. Set `remoteok_tags` to the topics you want. |
| `weworkremotely` | Remote jobs on We Work Remotely. Finds nothing until you list categories in `wwr_categories`. |
| `hn_who_is_hiring` | The monthly "Who is hiring?" post on Hacker News |
| `hn_jobs` | The Hacker News jobs page (companies funded by Y Combinator) |
| `greenhouse` | Company job pages on Greenhouse. List company board names in `greenhouse_boards`. |
| `ashby` | Company job pages on Ashby. List them in `ashby_boards`. |
| `lever` | Company job pages on Lever. List them in `lever_boards`. |
| `workable` | Company job pages on Workable. List accounts in `workable_accounts`. |
| `workday` | Company job pages on Workday. One `workday_boards` entry per company. |
| `linkedin`, `indeed` | LinkedIn and Indeed job search |
| `apple` | Apple's job site. Needs a Playwright browser. See [Install](install.md#playwright-browser-install-optional). |

For Greenhouse, Ashby, and Lever, `daily-driver jobs discover-boards` finds company pages that list your roles. You do not have to list every page yourself.

`daily-driver jobs run --list-sources` (or `daily-driver help sources`) prints the current list.

### Commands

```bash
daily-driver jobs run                           # search all sources and save new jobs in jobs.csv
daily-driver jobs run -n                        # preview only; nothing is saved
daily-driver jobs run --no-enrich               # save new jobs without the AI details
daily-driver jobs backfill                      # fill in empty details for saved jobs
daily-driver jobs backfill --limit 20           # use the AI model for at most 20 jobs
daily-driver jobs run -S remoteok,hn_jobs       # search only these sources
daily-driver jobs discover-boards               # find company job pages (Greenhouse, Ashby, Lever) that list your roles
daily-driver jobs promote https://acme.example/jobs/123   # copy a saved job into your tracker
daily-driver jobs status                        # show the last run and job counts by status
daily-driver jobs prune --older-than 30d        # archive finished jobs found more than 30 days ago
daily-driver jobs verify                        # close saved jobs that are no longer posted
```

- Press Ctrl-C to stop a search. Daily Driver keeps the jobs it has already found. If you stop while it is still searching, the AI details are skipped. Run `daily-driver jobs backfill` later to fill them in.
- `jobs promote` copies a saved job from `jobs.csv` into your tracker. Use it when you start to apply or interview. It does not change `jobs.csv`, and running it twice does not make a duplicate. Name the job with its full web address (URL), or with part of the company name that matches only one job.
- `jobs prune` moves finished jobs from `jobs.csv` to `jobs.archive.csv`. By default it moves jobs with the status `dropped`, `rejected`, `closed`, or `skipped` that were found more than 14 days ago. To use other statuses, add `-s STATUS`, for example `-s applied`. Nothing is deleted.

### Choosing an AI provider

By default, job search uses Claude to fill in job details. You can use a free AI model on your own computer instead (Ollama). This suits large runs of `jobs backfill`. Interactive chats always use Claude. For setup, see [Local AI setup](ollama-setup.md).

## Summary

`daily-driver summary` writes a summary of any period and copies it to your clipboard. You can also ask Claude for a summary in a chat.

```bash
daily-driver summary -r today
daily-driver summary -r week --detail high
daily-driver summary -r 2026-05-01:2026-05-09 --match project --match budget
```

| Option | What it does |
|---|---|
| `-r`, `--range` | The period. Use a date word from [Date words](#date-words), one date, or two dates such as `2026-05-01:2026-05-09`. |
| `--detail` | How much detail: `low`, `med`, or `high` |
| `--match` | Limits the summary to a keyword. Repeat the option for more keywords. |
| `--no-clipboard` | Skips copying to the clipboard |
| `--timeout SECONDS` | How long to wait for Claude. The default is 180 seconds. |

## Voice updates

`voice-profile.md` tells Claude how you write, so drafts sound like you. Update it from your own writing, such as Slack exports, emails, or blog posts:

```bash
daily-driver voice-update --from ~/exports/slack.txt ~/blog/
daily-driver voice-update --from ~/exports/slack.txt -n        # check your files only; nothing changes
daily-driver voice-update --from ~/exports/slack.txt --replace
```

- Give all files and folders after one `--from`. If you repeat `--from`, only the last one is used.
- Daily Driver reads `.md` and `.txt` files up to 500 kilobytes (KB) each.
- By default, new observations are added to your profile (`--append`). `--replace` starts over. Both save the old profile as `voice-profile.md.bak`.
- `-n` (`--dry-run`) checks that your files can be read. It does not change anything and does not show the new profile.

Use writing that you wrote yourself, not AI-generated text. The profile records your voice, not Claude's.

## Other commands

Claude runs many of these for you in a chat. You can also run them yourself:

| Command | What it does |
|---|---|
| `status` | Shows totals, items with no update in 14 days, and recent changes |
| `paths daily-plan` | Prints the path of today's plan file. Use `daily-notes` for the notes file. |
| `gather calendar` | Shows your macOS Calendar events |
| `gather git` | Shows recent Git commits from your projects |
| `calendar sync` | Copies today's plan time blocks to a macOS Calendar. Off until you set `calendar.sync_enabled: true`. The calendar named in `calendar.plan_calendar_name` (default `Daily Plan`) must exist. `day-start` runs it for you. |
| `help` | Shows lists of valid values: `help commands`, `help statuses`, `help categories`, `help sources`, `help dates` |

For the options of one command, run `daily-driver COMMAND --help`. For every command, see [Commands](commands.md).

## Scheduler (macOS)

The scheduler uses launchd, the macOS tool that runs tasks at set times. Daily Driver uses it to remind you to check in.

| Command | What it does |
|---|---|
| `daily-driver scheduler install` | Sets up the scheduled tasks |
| `daily-driver scheduler status` | Shows each task and whether it is installed |
| `daily-driver scheduler uninstall` | Removes the tasks |

These are the tasks and their default times:

| Task | Time | What it runs |
|---|---|---|
| `checkin` | 11:00 and 15:00 | `daily-driver check-in` (skipped while focus mode is on) |
| `day-start` | Off until you set `schedule.day_start` | `daily-driver day-start` |
| `day-end` | Off until you set `schedule.day_end` | `daily-driver day-end` |
| `jobs` | 07:00 (job search only) | `daily-driver jobs run` |
| `jobs-discover` | 23:59 on Saturday and Tuesday (job search only) | `daily-driver jobs discover-boards` |

When a task runs:

- `day-start`, `day-end`, and `check-in` show a macOS notification.
- Click the notification to open the chat in a new iTerm2 tab. Without iTerm2, it opens in Terminal.
- Clicking works only if `terminal-notifier` is installed. Without it, the notification shows the command to run. If clicking does nothing, see [Troubleshooting](troubleshooting.md).
- `jobs` and `jobs-discover` run in the background with no window.

To change the times or days, edit `.dd-config.yaml`:

```yaml
schedule:
  day_start: "09:00"
  day_end: "18:00"
  days: weekdays
scheduler:
  checkin: {times: ["10:30", "15:00"], days: weekdays}
  jobs:    {time: "23:59", days: [sun, wed]}
```

| Setting | Days it controls |
|---|---|
| `schedule.days` | `day-start` and `day-end` |
| `scheduler.checkin.days` | Check-in |
| `scheduler.jobs.days` | Job search run |
| `scheduler.discovery.days` | Board discovery |

A `days` setting is `daily`, `weekdays`, or a list of day names, such as `[sun, wed]`. The default is `daily`, except board discovery, which runs on Saturday and Tuesday.

After you change a setting, run `daily-driver scheduler install` again. If you switch job search off, `scheduler install` also removes its tasks. Logs are in `.daily-driver/state/logs/`.

## Troubleshooting

Full guide: [Troubleshooting](troubleshooting.md). This table shows where to start:

| Symptom | Where to look |
|---|---|
| A command does not work as expected | Run it again with `-v` for more detail or `-vv` for the most detail. `-q` shows errors only. |
| `doctor` shows an ERROR | Run `daily-driver doctor --fix`. Then see [`doctor` reports ERROR](troubleshooting.md#doctor-reports-error). |
| Scheduled reminders do not run, or run the wrong program | Run `daily-driver scheduler install` again. Then see [launchd plist won't load](troubleshooting.md#launchd-plist-wont-load). |
| Focus mode is stuck on | Run `daily-driver focus off`. Then see [Focus mode stuck on](troubleshooting.md#focus-mode-stuck-on). |
| Ollama connection refused, or model not downloaded | [Local AI setup](ollama-setup.md#troubleshooting) |
| Settings error after an upgrade | Read `CHANGELOG.md`, edit `.dd-config.yaml`, then run `daily-driver doctor`. Daily Driver does not change your settings for you. |

## More guides

- [Commands](commands.md): every command and option, plus a [command tree](cli-tree.md).
- [Settings](configuration.md): every setting in `.dd-config.yaml`.
- [Local AI setup](ollama-setup.md): run some AI tasks on your own computer.
- [Troubleshooting](troubleshooting.md): fixes for common problems.
