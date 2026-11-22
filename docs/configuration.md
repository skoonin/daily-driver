# Settings

Your settings live in `.dd-config.yaml` in your workspace folder. Most people change only a few of them, such as reminder times and task categories. The AI handles running daily-driver directly so your list, plans, and notes in your chats, so those need no settings.

How to edit the file:

- Every live line in the file that `init` creates shows its default value. Change a value to change how Daily Driver works.
- If you remove a setting, its default applies.
- A line that starts with `# ` is a sample. Samples cover settings that cannot show a live value: the shipped `claude` command templates, a provider of your own, and job search `locations`, `sources`, and `criteria`. Delete the `# ` to use one.
- Keep the spaces at the start of each line. They show which section a setting belongs to.
- Write times in quotes, for example `"08:30"`. Without quotes, a time such as `11:00` can be read as a number, and the setting may be rejected.
- Use each section name once. If a name appears twice, the second section replaces the first without an error.
- If a setting name is not known, the command stops and prints that name.
- Run `daily-driver doctor` after you edit the file. It reports mistakes.

For the daily flow, see [usage.md](usage.md). The AI reads `context.md` when it plans your day. Put your background there, and list tasks that repeat under `## Recurring Tasks`. Do not put them in this file.

## Sections

| Section | What it controls |
| --- | --- |
| [`ai`](#ai) | Which Artificial Intelligence (AI) runs each feature, and how `check-in` starts its chat |
| [`calendar`](#calendar) | Copy of your plan time blocks into the macOS Calendar app |
| [`focus`](#focus) | Default length of focus mode |
| [`gather`](#gather) | Folders that hold your git projects |
| [`schedule`](#schedule) | Times of the day-start and day-end reminders |
| [`scheduler`](#scheduler) | Times of check-in reminders |
| [`tracker`](#tracker) | Your list: categories and statuses. Required. |
| [`plugins`](#plugins) | Optional features. Job search is the only one |

## `ai`

Chooses which AI runs each feature. A provider is a program or a model server that Daily Driver calls. Leave this section out to run every feature on Claude Code.

Run day-start, day-end, check-in, and summary on opencode:

```yaml
ai:
  providers:
    opencode:
      tasks: [interactive, summary]
```

| Setting | Default | What it does |
| --- | --- | --- |
| `interactive.resume_check_in` | `false` | `true`: `check-in` continues the most recent chat in this workspace, from `day-start`, `day-end`, or `check-in`. `false`: `check-in` starts a new chat. Continuing needs a `resume` template on the provider. |
| `providers` | `{}` (the four built-in providers apply) | Your providers, by name. Each entry changes a built-in provider or adds a new one. |

### Built-in providers

| Provider | Kind | Default tasks | Notes |
| --- | --- | --- | --- |
| `claude` | Program: Claude Code | `interactive`, `summary`, `voice_update` | Runs every task that no provider claims. Runs up to 4 job reviews at the same time. |
| `opencode` | Program: opencode | none | Runs no task until you give it `tasks`. |
| `lmstudio` | Model server at `http://localhost:1234/v1` | none | Text tasks only. Has no `model`. |
| `ollama` | Model server at `http://localhost:11434/v1` | none | Text tasks only. Has no `model`. |

The file that `init` creates shows a live `claude` entry with `model`, `timeout`, and `tasks`, and also `max_parallel` when job search is on. Its command templates are `# ` samples. The shipped settings of every built-in are in [`providers.default.yaml`](../source/daily_driver/resources/templates/providers.default.yaml).

### Choose a provider for a task

A task is one job that Daily Driver hands to a provider.

| Task | Commands | Mode | Provider needs |
| --- | --- | --- | --- |
| `interactive` | `day-start`, `day-end`, `check-in`, `resume` | Interactive: a chat that you talk to | `run` template. `resume` needs a `resume` template too. |
| `summary` | `summary` | Agent: the provider runs in the workspace and reads the files that Daily Driver lists | `agent` template |
| `voice_update` | `voice-update` | Text: the prompt holds all the data, and the answer is text | `text` template, or `base_url` |
| `job_search.fit_notes` | `jobs run`, `jobs backfill` | Text | `text` template, or `base_url`. Job search only. |

- To run a core task on a provider, list the task under that provider's `tasks`. One provider can run several tasks. Two providers cannot run the same task.
- A core task that no provider lists runs on `claude`.
- A plugin picks its own provider. See [job search](job-search.md#ai-for-job-review).
- A provider that runs no task does nothing. `doctor` reports a provider that you defined and that runs no task.

### Set a model

```yaml
ai:
  providers:
    claude:
      model: sonnet
    lmstudio:
      model: qwen3-coder
      tasks: [voice_update]
```

- `model` has no default. Claude Code and opencode use their own default model. A model server needs a `model` to run a task.
- The `--model` option on a command wins over `model`.
- For a second model on the same provider, add an entry with `parent_provider`. See [AI providers](providers.md#change-or-add-a-provider).
- For an opencode session, `model` is also written to `opencode.json`. See [Sessions](providers.md#sessions).
- For models on your own computer, see [Local models](local-models.md).

For every provider setting, templates, time limits, and checks, see [AI providers](providers.md).

## `calendar`

When this is on, `day-start` copies the time blocks of your plan into the macOS Calendar app. A time block is a time range such as `09:00-10:30`. You can also run `daily-driver calendar sync` yourself. This works on macOS only.

```yaml
calendar:
  sync_enabled: true
  plan_calendar_name: Daily Plan
```

| Setting | Default | What it does |
| --- | --- | --- |
| `sync_enabled` | `false` | `true` turns on `calendar sync`. While `false`, `calendar sync` does nothing. |
| `plan_calendar_name` | `Daily Plan` | Name of the calendar to write to. Create this calendar in the Calendar app first. If it is missing, `calendar sync` does nothing and prints a notice. |

## `focus`

Focus mode pauses check-in reminders. Turn it on with `daily-driver focus on`.

```yaml
focus:
  default_duration: 45m
```

| Setting | Default | What it does |
| --- | --- | --- |
| `default_duration` | `25m` | Length of focus mode when `focus on` has no `--for` option. Examples: `30m`, `2h`, `1h30m`, or a number of minutes. |

## `gather`

The AI reads your recent git commits when it runs `day-start`, `check-in`, and `day-end`. This section tells it where to look.

```yaml
gather:
  git:
    search_paths: [~/git]
```

| Setting | Default | What it does |
| --- | --- | --- |
| `git.search_paths` | `[]` | Folders that hold your git projects. Use a full path or start with `~`. Daily Driver searches inside each folder, including subfolders. While the list is empty, it scans only the current folder. |

## `schedule`

The times of your day-start and day-end reminders. At each time, `scheduler install` adds a reminder. Click it to open the chat. Without `terminal-notifier`, the reminder shows the command to run. Install the tool with `brew install terminal-notifier`.

```yaml
schedule:
  day_start: "08:00"
  day_end: "17:30"
  days: weekdays
```

| Setting | Default | What it does |
| --- | --- | --- |
| `day_start` | none (no reminder) | Time of the day-start reminder, in quotes, such as `"08:00"`. |
| `day_end` | none (no reminder) | Time of the day-end reminder, in quotes, such as `"17:30"`. |
| `days` | `daily` | Days of both reminders: `daily`, `weekdays`, or a list of day names such as `[mon, wed, fri]`. |

- Run `daily-driver scheduler install` after you change these settings.
- A day-start that begins more than 2 hours after `day_start` counts as a late start, and the AI plans only the rest of the day. With `day_start` unset, a start after 11:00 counts as late.

## `scheduler`

The times of the check-in reminders. `daily-driver scheduler install` adds them to the macOS background scheduler. The times of job search runs are in [`plugins.job_search.scheduler`](job-search.md#scheduler).

Check in on weekdays only:

```yaml
scheduler:
  checkin: {times: ["10:30", "15:00"], days: weekdays}
```

| Setting | Default | What it does |
| --- | --- | --- |
| `checkin.enabled` | `true` | `false` stops the check-in reminders and keeps these settings. |
| `checkin.times` | `["11:00", "15:00"]` | Times of the check-in reminders. |
| `checkin.days` | `daily` | Days of the check-in reminders. |

- Times use the 24-hour clock in your local time. Write them in quotes.
- `days` is `daily`, `weekdays`, or a list of day names such as `[sun, wed]`. Day names can be short or full, in any case. An empty list is rejected; use `daily` instead.
- Each line you leave out keeps its default. For example, `checkin: {days: weekdays}` keeps the 11:00 and 15:00 check-ins and runs them on weekdays only.
- Run `daily-driver scheduler install` after every change. It removes the background job of a run that you turned off.

## `tracker`

Your list of tasks. The AI adds and updates entries for you in your chats. These settings choose your categories and which statuses are expected. This section is required.

Add a category that needs a link, and your own statuses:

```yaml
tracker:
  default_category: task
  categories:
    task:   {required: [title]}
    ticket: {required: [title, link]}
  extra_statuses: [waiting]
  terminal_statuses: [cancelled]
```

| Setting | Default | What it does |
| --- | --- | --- |
| `default_category` | `task` | The category that `daily-driver help categories` shows as the default. It must be one of your `categories`. |
| `categories` | `{task: {required: [title]}}` | Your categories. Each one lists the fields that a new entry must have. See [Custom tracker categories](#custom-tracker-categories). |
| `warn_unknown_status` | `true` | Shows a one-line warning when you set an unusual status. `false` turns the warning off. |
| `extra_statuses` | `[]` | Your own statuses. They never cause the warning. They do not apply to `job` entries. |
| `terminal_statuses` | `[]` | Your own statuses that close an entry. They add to the built-in closing statuses. |

Fields that a category's `required` list can name:

| Field | Meaning |
| --- | --- |
| `title` | Name of the entry |
| `link` | A web address |
| `notes` | Free text. The `--note` option fills it. |
| `next_action` | The next step |
| `due` | Due date |
| `status` | Status |
| `tags` | Tags |
| an `--extra` key name | A field you add with `tracker add --extra key=value` |

Statuses:

| Group | Statuses |
| --- | --- |
| Usual | `open`, `in-progress`, `blocked`, `done`, `ruled-out` |
| Always close an entry | `done`, `ruled-out`, `dropped`, `rejected`, `closed` |
| `job` category (job search only) | `found`, `pending`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, `closed` |

- A closed entry gets no follow-ups and is not reported as stalled.
- The warning ignores capital letters, spaces, and underscores. `Ruled_Out` counts as `ruled-out`.

## `plugins`

Optional features. Job search is the only plugin. It is off until a `plugins.job_search` section exists.

```yaml
plugins:
  job_search: {}
```

| Setting | Default | What it does |
| --- | --- | --- |
| `job_search` | none (off) | Job search. Every setting is in [Job search](job-search.md). |

## Customization

### Custom tracker categories

Categories are free-form. You or the AI can use any category name. List a category under `tracker.categories` only to require fields on it:

```yaml
tracker:
  default_category: task
  categories:
    task:    {required: [title]}
    ticket:  {required: [title]}
    contact: {required: [title]}
```

Fields that you add with `tracker add --extra key=value` are saved with the entry. They need no setting.

### Context and voice profile

`init` creates `context.md` and `voice-profile.md` in your workspace folder once. After that they are yours: edit them freely.

| File | What it holds | Who reads it |
| --- | --- | --- |
| `context.md` | Your background, goals, and routines. Tasks that repeat go under `## Recurring Tasks`. | The AI, in `day-start` and `day-end`. With job search on, the job review also reads it. |
| `voice-profile.md` | Your writing style. | The AI, before it drafts a message for you. |

`daily-driver voice-update --from <file>` rebuilds `voice-profile.md` from your writing samples.

### Custom Claude Code commands and agents

These are Claude Code features. Daily Driver gives other providers, such as opencode, their launcher instructions in a prompt file instead. See [Sessions](providers.md#sessions).

- Commands: put a markdown file at `.claude/commands/my-command.md`. Start a chat with `claude --add-dir <workspace>`, then type `/my-command`.
- Agents: put a markdown file at `.claude/agents/my-reviewer.md`. Start it with `claude --agent my-reviewer`.

Keep your own files outside the `daily-driver` folders. See [`.claude/` ownership](#claude-ownership).

### Change a shipped command

`daily-driver day-start` runs the command in `.claude/commands/daily-driver/day-start.md`. To change what `day-start` does, edit that file. `doctor --fix` keeps your edit. `doctor --reset` and `init --force` replace the file, so keep a copy outside the workspace.

A file with the same name one level up, `.claude/commands/day-start.md`, does not change `day-start`. It is a separate command that you start by hand with `/day-start`.

### `.claude/` ownership

Daily Driver manages five paths in your workspace: the `daily-driver` folders in `.claude/commands/` and `.claude/agents/`, `.claude/hooks/`, `README.md`, and `.claude/settings.local.json`. `doctor --fix` and `init` rewrite them after an upgrade and after you switch job search on or off. `doctor --fix` also rewrites them when a managed file is missing or invalid. `doctor --reset` and `init --force` always rewrite them. Everything else is yours.

| Path | `doctor --fix` and `init`, when they rewrite | `doctor --reset` and `init --force` |
| --- | --- | --- |
| `.claude/commands/daily-driver/` and `.claude/agents/daily-driver/` | Keep your edits to shipped files. Delete `.md` files you added. Delete a file that an older version shipped, unless you edited it. | Wipe the folders and write them again. You lose your edits and every file you added. |
| `.claude/hooks/` | The same rules, with `.sh` files in place of `.md` files. | Wipe the folder and write it again. You lose your edits and every file you added. |
| `README.md` in the workspace folder | Keeps your edits. | Overwrites the file. |
| `.claude/settings.local.json` | Keeps top-level settings you added. Replaces the shipped `$schema`, `permissions`, `hooks`, and `metadata` blocks, which removes allow rules that Claude Code saved. | The same. |
| `.claude/commands/user/`, `.claude/agents/user/`, top-level `.claude/commands/*.md` and `.claude/agents/*.md`, and everything else | Never changed. | Never changed. |

If `settings.local.json` has a format error, Daily Driver saves it as `settings.local.json.invalid` and writes a new file.

Allow rules that Claude Code saves in `.claude/settings.local.json` are lost when Daily Driver rewrites the file. Put your own allow rules in `~/.claude/settings.json` instead.

## See also

- [usage.md](usage.md): the daily flow.
- [AI providers](providers.md): every provider setting.
- [Job search](job-search.md): every job search setting.
- [Local models](local-models.md): run some AI tasks on your own computer.
- [commands.md](commands.md): every command and option.
