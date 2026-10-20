# Settings

Your settings live in `.dd-config.yaml` in your workspace folder. Most people change only a few of them, such as reminder times and task categories. Claude handles your list, plans, and notes in your chats, so those need no settings.

Most commands check this file each time they run. If a setting name is not known, the command stops and prints that name.

How to edit the file:

- In the file that `init` creates, a line that starts with `# ` is off. Delete the `# ` to turn it on.
- Keep the spaces at the start of each line. They show which section a setting belongs to.
- Write times in quotes, for example `"08:30"`. Without quotes, a time such as `11:00` can be read as a number, and the setting may be rejected.
- Use each section name once. If a name appears twice, the second section replaces the first without an error.
- Run `daily-driver doctor` after you edit the file. It reports mistakes.

For the daily flow, see [usage.md](usage.md). To run AI on your own computer, see [Local AI setup](ollama-setup.md).

## Sections

| Section | What it controls |
| --- | --- |
| [`tracker`](#tracker) | Your list: categories and statuses. Required. |
| [`schedule`](#schedule) | Times of the day-start and day-end reminders |
| [`scheduler`](#scheduler) | Times of check-in reminders and job runs |
| [`focus`](#focus) | Default length of focus mode |
| [`claude`](#claude) | How `check-in` starts its chat |
| [`gather`](#gather) | Folders that hold your git projects |
| [`calendar`](#calendar) | Copy of your plan time blocks into the macOS Calendar app |
| [`daily_driver`](#daily_driver) | Folder for your data files |
| [`ai`](#ai) | Which AI runs each feature: Claude or Ollama |
| [`plugins`](#pluginsjob_search) | Optional features. Job search is the only one |

Claude reads `context.md` when it plans your day. Put your background there, and list tasks that repeat under `## Recurring Tasks`. Do not put them in this file.

Some settings matter only when job search is on: `scheduler.jobs`, `scheduler.discovery`, `ai.claude`, and `ai.ollama.max_parallel`. With job search off, they are allowed but do nothing.

## `tracker`

Your list of tasks. Claude adds and updates entries for you in your chats. These settings choose your categories and which statuses are expected. This section is required.

| Setting | What it does | Default |
| --- | --- | --- |
| `default_category` | The category that `daily-driver help categories` shows as the default. It must be one of your `categories`. | `task` |
| `categories` | Your categories. Each one lists the fields that a new entry must have. | `task`, with `title` required |
| `warn_unknown_status` | Shows a one-line warning when you set an unusual status. Set to `false` to turn the warning off. | `true` |
| `extra_statuses` | Your own statuses. They never cause the warning. They do not apply to `job` entries. | none |
| `terminal_statuses` | Your own statuses that close an entry. Closed entries get no follow-ups and are not reported as stalled. | none |

A category's `required` list can name these fields: `title`, `link`, `notes`, `next_action`, `due`, `status`, `tags`, or the name of an `--extra` key. The option `--note` fills `notes`. To add a category, see [Custom tracker categories](#custom-tracker-categories).

Statuses:

- Usual statuses are `open`, `in-progress`, `blocked`, `done`, and `ruled-out`.
- Entries in the `job` category use `found`, `pending`, `skipped`, `applied`, `interviewing`, `rejected`, `dropped`, and `closed`.
- The statuses `done`, `ruled-out`, `dropped`, `rejected`, and `closed` always close an entry.
- The warning ignores capital letters, spaces, and underscores. `Ruled_Out` counts as `ruled-out`.

## `schedule`

The times of your day-start and day-end reminders. At each time, `scheduler install` adds a reminder. Click it to open the chat. Without `terminal-notifier`, the reminder shows the command to run. Install the tool with `brew install terminal-notifier`.

A day-start that begins more than 2 hours after `day_start` counts as a late start, and Claude plans only the rest of the day. With `day_start` unset, a start after 11:00 counts as late.

| Setting | What it does | Default |
| --- | --- | --- |
| `day_start` | Time of the day-start reminder, in quotes, such as `"08:00"`. | none (no reminder) |
| `day_end` | Time of the day-end reminder, in quotes, such as `"17:30"`. | none (no reminder) |
| `days` | Days of both reminders: `daily`, `weekdays`, or a list of day names such as `[mon, wed, fri]`. | `daily` |

Run `daily-driver scheduler install` after you change these settings.

## `scheduler`

Sets the times of the check-in reminders and, with job search on, the job runs. `daily-driver scheduler install` adds them to the macOS background scheduler. When this section is missing, these defaults apply:

| Run | Default time |
| --- | --- |
| Check-in reminders | 11:00 and 15:00, every day |
| Job search run (`jobs run`), job search only | 07:00, every day |
| Board search (`jobs discover-boards`), job search only | 23:59 on Saturday and Tuesday |

A board is a company's job page on a hiring site such as Greenhouse, Ashby, or Lever.

| Setting | What it does |
| --- | --- |
| `checkin.times` | Times of the check-in reminders, such as `["10:30", "15:00"]`. |
| `checkin.days` | Days of the check-in reminders. |
| `jobs.time` | Time of `jobs run`. |
| `jobs.days` | Days of `jobs run`. |
| `discovery.time` | Time of `jobs discover-boards`. |
| `discovery.days` | Days of `jobs discover-boards`. |

Rules:

- Times use the 24-hour clock in your local time. Write them in quotes.
- `days` is `daily`, `weekdays`, or a list of day names such as `[sun, wed]`. Day names can be short or full, in any case. An empty list is rejected; use `daily` instead.
- In a part you set, each line you leave out keeps its default. For example, `checkin: {days: weekdays}` keeps the 11:00 and 15:00 check-ins and runs them on weekdays only.
- To stop a run, clear its time: `checkin: {times: []}`, `jobs: {time: null}`, or `discovery: {time: null}`. Setting a whole part to `null` or `{}` does not stop it.
- Run `daily-driver scheduler install` after every change.
- With job search off, `jobs` and `discovery` do nothing. A full `scheduler install` removes their background jobs.
- Schedule `discovery` before `jobs`, for example the evening before. If a board search is skipped, `jobs run` uses the board list from the last one.

```yaml
scheduler:
  checkin:   {times: ["10:30", "15:00"], days: weekdays}
  jobs:      {time: "23:59", days: [sun, wed]}
  discovery: {time: "23:59", days: [sat, tue]}
```

## `focus`

Focus mode pauses check-in reminders. Turn it on with `daily-driver focus on`.

| Setting | What it does | Default |
| --- | --- | --- |
| `default_duration` | Length of focus mode when `focus on` has no `--for` option. Examples: `30m`, `2h`, `1h30m`, or a number of minutes. | `25m` |

## `claude`

Controls how `check-in` starts its Claude chat.

| Setting | What it does | Default |
| --- | --- | --- |
| `resume_check_in` | `true`: `check-in` continues the most recent chat in this workspace, from `day-start`, `day-end`, or `check-in`. `false`: `check-in` starts a new chat. | `false` |

## `gather`

Claude reads your recent git commits when it runs `day-start`, `check-in`, and `day-end`. This section tells it where to look.

| Setting | What it does | Default |
| --- | --- | --- |
| `git.search_paths` | Folders that hold your git projects. Use a full path or start with `~`, for example `~/git`. Daily Driver searches inside each folder, including subfolders. While the list is empty, it scans only the current folder. | none |

## `calendar`

When this is on, `day-start` copies the time blocks of your plan into the macOS Calendar app. A time block is a time range such as `09:00-10:30`. You can also run `daily-driver calendar sync` yourself. This works on macOS only and is off by default.

| Setting | What it does | Default |
| --- | --- | --- |
| `sync_enabled` | `true` turns on `calendar sync`. While `false`, `calendar sync` does nothing. | `false` |
| `plan_calendar_name` | Name of the calendar to write to. Create this calendar in the Calendar app first. If it is missing, `calendar sync` does nothing and prints a notice. | `Daily Plan` |

## `daily_driver`

The folder for your data files.

| Setting | What it does | Default |
| --- | --- | --- |
| `output_dir` | Folder for the tracker, notes, and other files. A relative path starts in the workspace folder. | `.` (the workspace folder) |

`jobs backfill` saves a copy of `jobs.csv` in `<output_dir>/backups/` before it changes the file. The name ends with the date and time in Coordinated Universal Time (UTC), for example `jobs.csv.bak.2026-05-13T05-45-00-123456Z`. `doctor` warns when more than 5 copies exist.

## Customization

### Custom tracker categories

Categories are free-form. You or Claude can use any category name. List a category under `tracker.categories` only to require fields on it:

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
| `context.md` | Your background, goals, and routines. Tasks that repeat go under `## Recurring Tasks`. | Claude, in `day-start` and `day-end`. With job search on, the job review also reads it. |
| `voice-profile.md` | Your writing style. | Claude, before it drafts a message for you. |

`daily-driver voice-update --from <file>` rebuilds `voice-profile.md` from your writing samples.

### Custom Claude commands and agents

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

## `ai`

Chooses which AI runs each feature: Claude or Ollama. Leave this section out to use Claude for everything. Ollama runs AI models on your own computer. For setup, see [Local AI setup](ollama-setup.md).

| Feature | Set its AI with | Commands |
| --- | --- | --- |
| Summary | `ai.summary` | `summary` |
| Voice update | `ai.voice_update` | `voice-update` |
| Job review | `plugins.job_search.enrichment` | `jobs run`, `jobs backfill` |
| Daily chats | Always Claude. `ai.interactive.model` sets the model. | `day-start`, `check-in`, `day-end` |

Each feature picks its provider (`claude` or `ollama`) and its model separately. For each one, the first of these that has a value wins:

1. The `--model` option on the command, where the command has one.
2. The feature's own setting, such as `ai.summary` or `plugins.job_search.enrichment.fit_notes`.
3. For job review only, the `plugins.job_search.enrichment` settings.
4. The `provider` and `model` of the `ai` section.
5. For the provider, `claude`. For the model, the provider's own default.

Set `ai.provider: ollama` once to use Ollama everywhere. Then set `ai.summary: { provider: claude }` to keep summaries on Claude.

Claude model names are `sonnet`, `opus`, and `haiku`. For Ollama, use the name of any model you downloaded, such as `qwen2.5:14b`. A model name must fit its provider. A Claude name on an Ollama feature makes that feature report an error when it runs.

`day-start`, `check-in`, and `day-end` always use Claude. Only `ai.interactive.model` changes their model. The global `ai.model` does not apply to them.

| Setting | What it does | Default |
| --- | --- | --- |
| `provider` | AI for every feature with no setting of its own: `claude` or `ollama`. | `claude` |
| `model` | Model for those features. Left out, the provider picks its own. | none |
| `summary.provider`, `summary.model` | AI for `summary`. | the two rows above |
| `voice_update.provider`, `voice_update.model` | AI for `voice-update`. | the two rows above |
| `interactive.model` | Claude model for `day-start`, `check-in`, and `day-end`. The `--model` option on the command wins. | Claude's own default |
| `claude.max_parallel` | Job reviews that run at the same time on Claude. `1` runs one at a time. Job search only. | `4` |
| `ollama.endpoint` | Address of the Ollama app. | `http://localhost:11434` |
| `ollama.timeout` | Seconds to wait for an answer. | `60` |
| `ollama.max_parallel` | Job reviews that run at the same time on Ollama. `1` runs one at a time. Job search only. See [Local AI setup](ollama-setup.md) for memory limits. | `4` |

`max_parallel` changes speed only. It does not change how many jobs are reviewed (`max_enrich_fit`) or the time limit for one call.

Example: use Ollama by default, but keep summaries and voice updates on Claude. The `plugins` part turns job search on.

```yaml
ai:
  provider: ollama            # AI for every feature without its own setting
  model: qwen2.5:14b
  summary: { provider: claude, model: sonnet }
  voice_update: { provider: claude }
  ollama:
    endpoint: http://localhost:11434
    timeout: 90
plugins:
  job_search:
    enrichment:
      provider: ollama        # AI for job review
      model: phi4
      fit_notes: { provider: claude, model: sonnet }   # the fit and notes call uses Claude
```

## `plugins.job_search`

Job search finds jobs on the sites you choose, removes duplicates, and scores how well each job fits you. It is off until this section exists. `init --job-search` writes the section. `jobs run` also needs `scraper.enabled: true` and at least one entry under `sources`. `context.md` is not rewritten after `init`. Add your location preferences and what you look for in a role to it by hand. To switch job search on or off in an existing workspace, see [Enable it](usage.md#enable-it).

| Setting | What it does | Default |
| --- | --- | --- |
| `enabled` | Job search is on while this section exists. `false` turns it off and keeps your settings. | `true` |
| `persona` | Who you are as a candidate. The AI uses it to score each job. Left out, it scores jobs for a site reliability, platform, or infrastructure engineer. | none |
| `roles` | Job titles to keep. `*` matches any text. A leading `!` removes titles. See [Which jobs are kept](#which-jobs-are-kept). | none |
| `domain_keywords` | Words for your field. See [Which jobs are kept](#which-jobs-are-kept). | built-in list |
| `seniority_keywords` | Words for your level, such as `senior`. See [Which jobs are kept](#which-jobs-are-kept). | built-in list |
| `locations` | Which places to keep. See [`locations`](#locations). | keep all places |
| `scraper` | How Daily Driver reads job sites. See [`scraper`](#scraper). | see below |
| `enrichment` | The AI review of each new job. See [`enrichment`](#enrichment). | see below |
| `verify` | When `jobs verify` checks saved jobs. See [`verify`](#verify). | see below |
| `discovery` | How `jobs discover-boards` works. See [`discovery`](#discovery). | see below |
| `sources` | The sites to search. See [`sources`](#sources). | none |

### Which jobs are kept

Only the job title and the location remove jobs. Pay is shown only: Daily Driver writes any pay it finds to the `Comp` column and never removes a job for its pay.

A job title is kept when one of these is true:

- It contains one of your `roles`. Matching ignores capital letters. A `*` matches any text.
- It contains one word from `domain_keywords` and one word from `seniority_keywords`.

A title that matches a role that starts with `!` is always removed, for example `"!Manager"`.

If `domain_keywords` or `seniority_keywords` is empty, a built-in list is used instead. The built-in lists hold site reliability and infrastructure words, such as `sre` and `devops`, and level words, such as `senior` and `staff`. If you search for another kind of job, fill in both lists. An empty `roles` list matches no title by itself, but the two keyword lists can still keep a title.

### `locations`

Chooses which places to keep. With no `locations` section, no job is removed for its place.

| Setting | What it does | Default |
| --- | --- | --- |
| `home_city` | Your city. The AI uses it to score each job. Set it to your own city. Left out, it is `Vancouver, BC`. | none |
| `remote` | `true` keeps remote jobs. | `false` |
| `remote_unlisted_countries` | `true` keeps remote jobs from any country, not only those in `countries`. | `false` |
| `countries` | The countries to keep, each with a list of cities. | none |

`countries` maps a two-letter country code to a list of cities, for example `{CA: [Vancouver, Victoria], US: []}`.

- `[]` keeps jobs from anywhere in that country.
- A list of cities keeps only jobs whose location names one of those cities. The country name does not have to appear.
- A job that names only the country does not match a country that has a city list.
- Write each city as job boards write it, for example `Montréal`. A blank city name is an error.
- An empty `countries: {}` keeps no onsite job. Only remote jobs stay, and only when `remote: true`.

By default, a remote job stays when its location names a country from `countries`, or names no country, such as `Remote`. City lists do not apply to remote jobs. A remote job that names another country is removed. Set `remote_unlisted_countries: true` to keep it.

### `scraper`

How Daily Driver reads job sites. These settings apply to every source.

| Setting | What it does | Default |
| --- | --- | --- |
| `enabled` | Turns on `jobs run`. While `false`, `jobs run` finds nothing and prints `Scraper disabled`. | `false` |
| `search_terms` | Search words for LinkedIn, Indeed, and Apple. Left empty, they come from `roles`. | none |
| `max_age_days` | Skips Hacker News job posts (`hn_jobs`) older than this many days. `0` means no limit. | `30` |
| `max_pages` | Result pages read for each Apple search. | `3` |
| `timeout` | Seconds to wait for a site. | `30` |
| `max_retries` | Retries when a site is busy. `0` means no retries. | `3` |
| `parallel_workers` | Sites searched at the same time. | `8` |
| `browser` | Browser for the Apple source: `firefox`, `chromium`, or `webkit`. | `firefox` |
| `user_agent` | How Daily Driver names itself to job sites. The default looks like Firefox 128. | a Firefox 128 name |
| `company_noise_words` | Words ignored when Daily Driver decides that two jobs come from the same company. | built-in list |

`browser` is the browser that Daily Driver opens for the Apple source. `doctor` checks that it is installed, and `daily-driver doctor --fix` installs it.

`company_noise_words` are words such as `inc`, `ltd`, and `labs`. Leave the list empty to use the built-in list. If you list words, your list replaces the built-in list. The company name saved in `jobs.csv` does not change.

### `enrichment`

Enrichment is the AI review of each new job. It adds a fit score, a short note, and a remote, hybrid, or onsite label. It uses one AI call for each job.

The review also reads `context.md` from your workspace folder. Your experience and preferences in that file change the fit score and the notes. Without the file, the score uses only the role, company, and location.

| Setting | What it does | Default |
| --- | --- | --- |
| `provider` | AI for job review: `claude` or `ollama`. Left out, the `ai` section decides. | none |
| `model` | Model for job review, such as `sonnet` or `qwen2.5:14b`. Left out, the provider picks its own. | none |
| `fit_notes.provider`, `fit_notes.model` | AI for the fit and notes call. They win over the two rows above. | none |
| `enrich_fit` | Scores each job from 1 to 10 for fit. Set to `false` to turn off the whole job review, including the note, the remote label, and `criteria`. | `true` |
| `enrich_notes` | Adds a one-line note that explains the score: the main match or mismatch, and the location fit. Set to `false` to turn off the whole job review, the same as `enrich_fit`. | `true` |
| `enrich_is_remote` | Labels each job `remote`, `hybrid`, or `onsite`. It uses the same AI call. | `true` |
| `max_enrich_fit` | Most jobs reviewed per run. | `50` |
| `enrich_timeout` | Seconds before one review gives up. | `30` |
| `detail_delay_seconds` | Seconds to pause between job page downloads. | `0.5` |
| `max_description_words` | Words of each posting that the AI reads. The minimum is 100. Pay and visa details are often near the end, so a low number can miss them. | `2000` |
| `force_recook_cooldown_hours` | With `jobs backfill --force-update`, skips jobs reviewed within this many hours, so an interrupted run can continue. `0` reviews all jobs again. `missing` reviews only jobs with no review yet. `--cooldown-hours` overrides it for one run. | `24` |
| `criteria` | Your own questions about each job. See [`criteria`](#criteria). | none |

For `provider` and `model`, see the order in [`ai`](#ai).

#### `criteria`

`criteria` lists your own questions about a job. For each job, the AI answers each question from the job post. A useful answer is added to the job's Notes column as `Label: answer`. Nothing is added when the post does not say. Questions do not add AI calls. Each label must be different.

| Setting | What it does |
| --- | --- |
| `label` | Short name shown in Notes, for example `Sponsorship`. |
| `assess` | The question, in plain words, for example `Does the role offer visa sponsorship?` |

A job with the note `Kubernetes shop` and a sponsorship answer of `Yes, H-1B` gets the note `Kubernetes shop | Sponsorship: Yes, H-1B`. A job that says nothing about sponsorship keeps `Kubernetes shop`.

```yaml
enrichment:
  criteria:
    - label: Sponsorship
      assess: Does the role offer visa sponsorship?
    - label: Clearance
      assess: Is a US security clearance required?
```

### `verify`

`jobs verify` closes saved jobs that are no longer posted. See [commands.md](commands.md) for the command. The age of a job counts from the `Date Verified` column, or from `Date Found` when `Date Verified` is empty.

| Setting | What it does | Default |
| --- | --- | --- |
| `reverify_days` | Checks a job's link again after this many days. The minimum is 1. | `7` |
| `unverified_age_days` | Closes jobs that have no link to check (Indeed, Hacker News) after this many days. The minimum is 1. | `30` |

### `discovery`

`jobs discover-boards` finds company job boards that list jobs matching your roles. One run is a sweep. Later sweeps check known boards again. These settings control how often, and how failures are reported.

| Setting | What it does | Default |
| --- | --- | --- |
| `reprobe_days` | Checks a known board again after this many days. The minimum is 1. | `30` |
| `max_reprobe_per_sweep` | Most boards checked again per sweep. The most overdue boards go first. The minimum is 1. | `500` |
| `degraded_failure_ratio` | A site is reported as failing when this share of its boards fail. `0.25` means 25%. The value must be above 0 and at most 1. | `0.25` |
| `dormant_after_empty_sweeps` | A board that lists no jobs this many checks in a row is checked less often. `0` turns this off. | `2` |
| `dormant_reprobe_multiplier` | How much less often. With `6`, a 30-day check becomes a 180-day check. `1` keeps the normal timing. | `6` |

### `sources`

The sites to search. Only sources listed here run. Each source has `enabled` (`true` or `false`). A source with settings but no `enabled` line is on. A short form also works: `apple: true`.

`daily-driver jobs run --list-sources` (or `daily-driver help sources`) prints the sources that exist.

| Source | Settings (default) |
| --- | --- |
| `remoteok` | `remoteok_tags` (none) |
| `weworkremotely` | `wwr_categories` (none). It finds nothing until you list a category. |
| `greenhouse` | `greenhouse_boards` (none), `exclude_boards` (none) |
| `ashby` | `ashby_boards` (none), `exclude_boards` (none) |
| `lever` | `lever_boards` (none), `exclude_boards` (none) |
| `workable` | `workable_accounts` (none) |
| `workday` | `workday_boards` (none) |
| `hn_who_is_hiring`, `hn_jobs` | `hn_max_posts` (500) |
| `linkedin` | `results_wanted_per_query` (50), `hours_old` (168), `max_city_queries` (40) |
| `indeed` | `results_wanted_per_query` (50), `hours_old` (168), `max_city_queries` (40), `country` (`USA`) |
| `apple` | On or off only. It needs the browser set in `scraper.browser`. |

Notes on the board sources:

- `greenhouse_boards`, `ashby_boards`, and `lever_boards` pin boards. Daily Driver always reads a pinned board, even when `jobs discover-boards` found no matching jobs there. Jobs from a pinned board must still match your roles.
- Each run reads your pinned boards and the boards found by `jobs discover-boards`, minus `exclude_boards`. `exclude_boards` wins over both. Spell its entries exactly, capitals included.
- Ashby board names are case-sensitive.
- Name a board as it appears in its link: `boards.greenhouse.io/<name>` for Greenhouse, `jobs.ashbyhq.com/<name>` for Ashby, `jobs.lever.co/<name>` for Lever, and `apply.workable.com/<name>` for Workable.
- A `workday_boards` entry names the three parts of a Workday link such as `companyh.wd5.myworkdayjobs.com/companyhcareers`: `tenant` (`companyh`), `host` (`wd5`), and `site` (`companyhcareers`). `company` is an optional display name. Left out, it is the tenant name in title case.
- `wwr_categories` entries are the category names in WeWorkRemotely feed links such as `weworkremotely.com/categories/remote-<category>-jobs`.

LinkedIn and Indeed settings:

| Setting | What it does | Default |
| --- | --- | --- |
| `results_wanted_per_query` | Results per search. | `50` |
| `hours_old` | Skips jobs older than this many hours. `168` is 7 days. | `168` |
| `max_city_queries` | Most city-by-city searches per run. | `40` |
| `country` | Indeed only. The Indeed site to use when one of your countries has no Indeed site. LinkedIn has no `country` setting. | `USA` |

Each search runs once for each country. It runs again for each city you list in `locations.countries`, so a local job competes only with jobs from its own city. `max_city_queries` is the most city searches in one run, for LinkedIn and for Indeed separately. The run output says how many city searches it skipped. A country with an empty city list adds no city search.

LinkedIn and Indeed run as separate searches. A failure in one does not stop the other. Glassdoor and Google Jobs are not available.

```yaml
sources:
  weworkremotely:
    enabled: true
    wwr_categories: [devops-sysadmin]
  greenhouse:
    enabled: true
    greenhouse_boards: [company-a, company-b]  # always read
    exclude_boards: []                         # never read, even when discovery finds them
  ashby:
    enabled: true
    ashby_boards: [company-d, company-e]
  lever:
    enabled: true
    lever_boards: [company-g]
  workable:
    enabled: true
    workable_accounts: [huggingface]
  workday:
    enabled: true
    workday_boards:
      - {tenant: companyh, host: wd5, site: companyhcareers, company: Company H}
  linkedin:
    enabled: true
    results_wanted_per_query: 50
    hours_old: 168
  indeed:
    enabled: true
    results_wanted_per_query: 50
    hours_old: 168
    country: USA
```

### Job search example

A fuller example for a job search workspace:

```yaml
daily_driver:
  output_dir: output

tracker:
  default_category: task
  categories:
    task:    {required: [title]}
    job:     {required: [title]}
    errand:  {required: [title]}
    contact: {required: [title]}

scheduler:
  checkin:     {times: ["10:30", "15:00"]}
  jobs: {time: "06:30"}

plugins:
  job_search:
    persona: "Experienced project coordinator, open to remote roles"
    roles: ["Project Coordinator", "Program Coordinator", "!Intern"]
    locations:
      home_city: Vancouver, BC
      remote: true
      countries: {US: [], CA: [Vancouver, Victoria]}
    scraper:
      enabled: true
    enrichment:
      enrich_fit: true
      enrich_notes: true
      criteria:
        - label: Sponsorship
          assess: Does the role offer visa sponsorship?
    sources:
      greenhouse:
        enabled: true
        greenhouse_boards: [company-a, company-b, company-c]
```

## See also

- [usage.md](usage.md): the daily flow.
- [Local AI setup](ollama-setup.md): run some AI tasks on your own computer.
- [commands.md](commands.md): every command and option.
