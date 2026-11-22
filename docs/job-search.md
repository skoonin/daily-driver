# `plugins.job_search`

Job search finds jobs on the sites you choose, removes duplicates, and scores how well each job fits you. It is off until the `plugins.job_search` section exists. `init --job-search` writes the section. `jobs run` also needs `scraper.enabled: true` and at least one entry under `sources`. To switch job search on or off in an existing workspace, see [Enable it](usage.md#enable-it). For the commands, see [commands.md](commands.md).

`context.md` is not rewritten after `init`. Add your location preferences and what you look for in a role to it by hand. The Artificial Intelligence (AI) review reads it.

Search for project coordinator jobs, remote or in two cities:

```yaml
plugins:
  job_search:
    roles: ["Project Coordinator", "Program Coordinator", "!Intern"]
    locations:
      home_city: Vancouver, BC
      remote: true
      countries: {CA: [Vancouver, Victoria]}
    scraper:
      enabled: true
    sources:
      greenhouse:
        enabled: true
        greenhouse_boards: [company-a, company-b]
```

| Setting | Default | What it does |
| --- | --- | --- |
| `enabled` | `true` | Job search is on while this section exists. `false` turns it off and keeps your settings. |
| `roles` | `[]` | Job titles to keep. `*` matches any text. A leading `!` removes titles. See [Which jobs are kept](#which-jobs-are-kept). |
| `domain_keywords` | `[]` (built-in list is used) | Words for your field. See [Which jobs are kept](#which-jobs-are-kept). |
| `seniority_keywords` | `[]` (built-in list is used) | Words for your level, such as `senior`. See [Which jobs are kept](#which-jobs-are-kept). |
| `locations` | none (keeps every place) | Which places to keep. See [`locations`](#locations). |
| `provider` | none (`claude`) | AI for job review. The name of a provider under `ai.providers`. |
| `tasks` | `{}` | AI for one task of job search. `fit_notes` is the only one. It names a provider and wins over `provider`. |
| `scraper` | see [`scraper`](#scraper) | How Daily Driver reads job sites. |
| `enrichment` | see [`enrichment`](#enrichment) | The AI review of each new job. |
| `verify` | see [`verify`](#verify) | When `jobs verify` checks saved jobs. |
| `discovery` | see [`discovery`](#discovery) | How `jobs discover-boards` works. |
| `scheduler` | see [`scheduler`](#scheduler) | When `jobs run` and `jobs discover-boards` run in the background. |
| `sources` | `{}` | The sites to search. See [`sources`](#sources). |

One other setting matters only when job search is on: `max_parallel` on a provider. See [Providers](providers.md).

## AI for job review

Job search has one AI task, `fit_notes`. It scores each job, writes the note, and labels the job remote, hybrid, or onsite. `jobs run` and `jobs backfill` run it. It is a text task, so a provider needs a `text` template, or a `base_url` and a `model`. See [AI providers](providers.md).

Run job reviews on LM Studio:

```yaml
ai:
  providers:
    lmstudio:
      model: qwen3-coder
plugins:
  job_search:
    provider: lmstudio
```

The provider is the first of these that is set:

1. `plugins.job_search.tasks.fit_notes`
2. `plugins.job_search.provider`
3. `claude`

- Each one names a provider under `ai.providers`, or a built-in provider. Daily Driver reports a name that is not defined, and a task name that job search does not have, when it reads the config.
- The number of reviews that run at once is the `max_parallel` of the provider.

## Which jobs are kept

Only the job title and the location remove jobs. Pay is shown only: Daily Driver writes any pay it finds to the `Comp` column and never removes a job for its pay.

A job title is kept when one of these is true:

- It contains one of your `roles`. Matching ignores capital letters. A `*` matches any text.
- It contains one word from `domain_keywords` and one word from `seniority_keywords`.

A title that matches a role that starts with `!` is always removed, for example `"!Manager"`.

- If `domain_keywords` or `seniority_keywords` is empty, a built-in list is used instead. The built-in lists hold site reliability and infrastructure words, such as `sre` and `devops`, and level words, such as `senior` and `staff`.
- If you search for another kind of job, fill in both lists.
- An empty `roles` list matches no title by itself, but the two keyword lists can still keep a title.

## `locations`

Chooses which places to keep. With no `locations` section, no job is removed for its place.

Keep remote jobs, all of the US, and two Canadian cities:

```yaml
locations:
  home_city: Vancouver, BC
  remote: true
  countries: {CA: [Vancouver, Victoria], US: []}
```

| Setting | Default | What it does |
| --- | --- | --- |
| `home_city` | none (the AI uses `Vancouver, BC`) | Your city. The AI uses it to score each job. Set it to your own city. |
| `remote` | `false` | `true` keeps remote jobs. |
| `remote_unlisted_countries` | `false` | `true` keeps remote jobs from any country, not only those in `countries`. |
| `countries` | `{}` | The countries to keep. Each is a two-letter country code with a list of cities. |

Rules for `countries`:

- `[]` keeps jobs from anywhere in that country.
- A list of cities keeps only jobs whose location names one of those cities. The country name does not have to appear.
- A job that names only the country does not match a country that has a city list.
- Write each city as job boards write it, for example `Montréal`. A blank city name is an error.
- An empty `countries: {}` keeps no onsite job. Only remote jobs stay, and only when `remote: true`.

Rules for remote jobs:

- A remote job stays when its location names a country from `countries`, or names no country, such as `Remote`.
- City lists do not apply to remote jobs.
- A remote job that names another country is removed. Set `remote_unlisted_countries: true` to keep it.

## `scraper`

How Daily Driver reads job sites. These settings apply to every source.

Turn on `jobs run` and search for your own words:

```yaml
scraper:
  enabled: true
  search_terms: ["platform engineer", "site reliability engineer"]
```

| Setting | Default | What it does |
| --- | --- | --- |
| `enabled` | `false` | Turns on `jobs run`. While `false`, `jobs run` finds nothing and prints `Scraper disabled`. |
| `search_terms` | none (uses `roles`) | Search words for LinkedIn, Indeed, and Apple. Left out, they come from `roles`. |
| `max_age_days` | `30` | Skips Hacker News job posts (`hn_jobs`) older than this many days. `0` means no limit. |
| `max_pages` | `3` | Result pages read for each Apple search. |
| `timeout` | `30` | Seconds to wait for a site. |
| `max_retries` | `3` | Retries when a site is busy. `0` means no retries. |
| `parallel_workers` | `8` | Sites searched at the same time. |
| `browser` | `firefox` | Browser for the Apple source: `firefox`, `chromium`, or `webkit`. `doctor` checks that it is installed, and `daily-driver doctor --fix` installs it. |
| `user_agent` | a Firefox 128 name | How Daily Driver names itself to job sites. |
| `company_noise_words` | `[]` (built-in list is used) | Words ignored when Daily Driver decides that two jobs come from the same company, such as `inc`, `ltd`, and `labs`. |

- If you list `company_noise_words`, your list replaces the built-in list.
- The company name saved in `jobs.csv` does not change.

## `enrichment`

Enrichment is the AI review of each new job. It adds a fit score, a short note, and a remote, hybrid, or onsite label. It uses one AI call for each job. The review also reads `context.md`. Without that file, the score uses only the role, company, and location.

Review more jobs per run and give each review more time:

```yaml
enrichment:
  max_enrich_fit: 100
  enrich_timeout: 60
```

| Setting | Default | What it does |
| --- | --- | --- |
| `enrich_fit` | `true` | Scores each job from 1 to 10 for fit. `false` turns off the whole job review, including the note, the remote label, and `criteria`. |
| `enrich_notes` | `true` | Adds a one-line note that explains the score: the main match or mismatch, and the location fit. `false` turns off the whole job review, the same as `enrich_fit`. |
| `enrich_is_remote` | `true` | Labels each job `remote`, `hybrid`, or `onsite`. It uses the same AI call. |
| `max_enrich_fit` | `50` | Most jobs reviewed per run. |
| `enrich_timeout` | `30` | Seconds before one review gives up. The `timeout` of a provider does not apply to job reviews. |
| `detail_delay_seconds` | `0.5` | Seconds to pause between job page downloads. |
| `max_description_words` | `2000` | Words of each posting that the AI reads. The minimum is 100. Pay and visa details are often near the end, so a low number can miss them. |
| `force_recook_cooldown_hours` | `24` | With `jobs backfill --force-update`, skips jobs reviewed within this many hours, so an interrupted run can continue. `0` reviews all jobs again. `missing` reviews only jobs with no review yet. `--cooldown-hours` overrides it for one run. |
| `criteria` | `[]` | Your own questions about each job. See [`criteria`](#criteria). |

The provider that runs the review is set by `plugins.job_search.provider` and `plugins.job_search.tasks.fit_notes`, not here. See [AI for job review](#ai-for-job-review).

`jobs backfill` saves a copy of `jobs.csv` in `<workspace>/backups/` before it changes the file. The name ends with the date and time in Coordinated Universal Time (UTC), for example `jobs.csv.bak.2026-05-13T05-45-00-123456Z`. `doctor` warns when more than 5 copies exist.

### `criteria`

`criteria` lists your own questions about a job. For each job, the AI answers each question from the job post. A useful answer is added to the Notes column of the job as `Label: answer`. Nothing is added when the post does not say. Questions do not add AI calls. Each label must be different.

```yaml
enrichment:
  criteria:
    - label: Sponsorship
      assess: Does the role offer visa sponsorship?
    - label: Clearance
      assess: Is a US security clearance required?
```

| Setting | Default | What it does |
| --- | --- | --- |
| `label` | none (required) | Short name shown in Notes, for example `Sponsorship`. |
| `assess` | none (required) | The question, in plain words, for example `Does the role offer visa sponsorship?` |

A job with the note `Kubernetes shop` and a sponsorship answer of `Yes, H-1B` gets the note `Kubernetes shop | Sponsorship: Yes, H-1B`. A job that says nothing about sponsorship keeps `Kubernetes shop`.

## `verify`

`jobs verify` closes saved jobs that are no longer posted. The age of a job counts from the `Date Verified` column, or from `Date Found` when `Date Verified` is empty.

```yaml
verify:
  reverify_days: 14
```

| Setting | Default | What it does |
| --- | --- | --- |
| `reverify_days` | `7` | Checks the link of a job again after this many days. The minimum is 1. |
| `unverified_age_days` | `30` | Closes jobs that have no link to check (Indeed, Hacker News) after this many days. The minimum is 1. |

## `discovery`

`jobs discover-boards` finds company job boards that list jobs matching your roles. A board is a company job page on a hiring site such as Greenhouse, Ashby, or Lever. One run is a sweep. Later sweeps check known boards again. These settings control how often, and how failures are reported.

```yaml
discovery:
  reprobe_days: 60
```

| Setting | Default | What it does |
| --- | --- | --- |
| `reprobe_days` | `30` | Checks a known board again after this many days. The minimum is 1. |
| `max_reprobe_per_sweep` | `500` | Most boards checked again per sweep. The most overdue boards go first. The minimum is 1. |
| `degraded_failure_ratio` | `0.25` | A site is reported as failing when this share of its boards fail. `0.25` means 25%. The value must be above 0 and at most 1. |
| `dormant_after_empty_sweeps` | `2` | A board that lists no jobs this many checks in a row is checked less often. `0` turns this off. |
| `dormant_reprobe_multiplier` | `6` | How much less often. With `6`, a 30-day check becomes a 180-day check. `1` keeps the normal timing. |

## `scheduler`

When `jobs run` and `jobs discover-boards` run in the background. `daily-driver scheduler install` adds them to the macOS background scheduler.

Run job search twice a week, and do not look for new boards:

```yaml
plugins:
  job_search:
    scheduler:
      run: {time: "23:59", days: [sun, wed]}
      discovery: {enabled: false}
```

| Setting | Default | What it does |
| --- | --- | --- |
| `run.enabled` | `true` | `false` stops the `jobs run` background job and keeps these settings. |
| `run.time` | `"07:00"` | Time of `jobs run`. |
| `run.days` | `daily` | Days of `jobs run`. |
| `discovery.enabled` | `true` | `false` stops the `jobs discover-boards` background job and keeps these settings. |
| `discovery.time` | `"23:59"` | Time of `jobs discover-boards`. |
| `discovery.days` | `[sat, tue]` | Days of `jobs discover-boards`. |

- Times use the 24-hour clock in your local time. Write them in quotes.
- `days` is `daily`, `weekdays`, or a list of day names such as `[sun, wed]`.
- Each line you leave out keeps its default.
- Run `daily-driver scheduler install` after every change. It removes the background job of a run that you turned off.
- Schedule `discovery` before `run`, for example the evening before. If a board search is skipped, `jobs run` uses the board list from the last one.

## `sources`

The sites to search. Only sources listed here run. Each source has `enabled` (`true` or `false`). A source with settings but no `enabled` line is on. A short form also works: `apple: true`. `daily-driver jobs run --list-sources` (or `daily-driver help sources`) prints the sources that exist.

```yaml
sources:
  weworkremotely:
    enabled: true
    wwr_categories: [devops-sysadmin]
  greenhouse:
    enabled: true
    greenhouse_boards: [company-a, company-b]  # always read
    exclude_boards: []                         # never read, even when discovery finds them
  workday:
    enabled: true
    workday_boards:
      - {tenant: companyh, host: wd5, site: companyhcareers, company: Company H}
  linkedin:
    enabled: true
  apple: true
```

| Source | Setting | Default |
| --- | --- | --- |
| every source | `enabled` | `true` |
| `remoteok` | `remoteok_tags` | `[]` |
| `weworkremotely` | `wwr_categories` | `[]` (finds nothing until you list a category) |
| `greenhouse` | `greenhouse_boards`, `exclude_boards` | `[]`, `[]` |
| `ashby` | `ashby_boards`, `exclude_boards` | `[]`, `[]` |
| `lever` | `lever_boards`, `exclude_boards` | `[]`, `[]` |
| `workable` | `workable_accounts` | `[]` |
| `workday` | `workday_boards` | `[]` |
| `hn_who_is_hiring`, `hn_jobs` | `hn_max_posts` | `500` |
| `linkedin` | `results_wanted_per_query`, `hours_old`, `max_city_queries` | `50`, `168`, `40` |
| `indeed` | `results_wanted_per_query`, `hours_old`, `max_city_queries`, `country` | `50`, `168`, `40`, `USA` |
| `apple` | none | On or off only. It needs the browser set in `scraper.browser`. |

Board sources:

- `greenhouse_boards`, `ashby_boards`, and `lever_boards` pin boards. Daily Driver always reads a pinned board, even when `jobs discover-boards` found no matching jobs there. Jobs from a pinned board must still match your roles.
- Each run reads your pinned boards and the boards found by `jobs discover-boards`, minus `exclude_boards`. `exclude_boards` wins over both. Spell its entries exactly, capitals included.
- Ashby board names are case-sensitive.
- Name a board as it appears in its link: `boards.greenhouse.io/<name>` for Greenhouse, `jobs.ashbyhq.com/<name>` for Ashby, `jobs.lever.co/<name>` for Lever, and `apply.workable.com/<name>` for Workable.
- A `workday_boards` entry names the three parts of a Workday link such as `companyh.wd5.myworkdayjobs.com/companyhcareers`: `tenant` (`companyh`), `host` (`wd5`), and `site` (`companyhcareers`). `company` is an optional display name. Left out, it is the tenant name in title case.
- `wwr_categories` entries are the category names in WeWorkRemotely feed links such as `weworkremotely.com/categories/remote-<category>-jobs`.

LinkedIn and Indeed:

| Setting | Default | What it does |
| --- | --- | --- |
| `results_wanted_per_query` | `50` | Results per search. |
| `hours_old` | `168` | Skips jobs older than this many hours. `168` is 7 days. |
| `max_city_queries` | `40` | Most city-by-city searches per run, for LinkedIn and for Indeed separately. |
| `country` | `USA` | Indeed only. The Indeed site to use when one of your countries has no Indeed site. LinkedIn has no `country` setting. |

- Each search runs once for each country. It runs again for each city you list in `locations.countries`, so a local job competes only with jobs from its own city.
- The run output says how many city searches it skipped. A country with an empty city list adds no city search.
- LinkedIn and Indeed run as separate searches. A failure in one does not stop the other.
- Glassdoor and Google Jobs are not available.

## Full example

A job search workspace:

```yaml
tracker:
  default_category: task
  categories:
    task:    {required: [title]}
    job:     {required: [title]}
    errand:  {required: [title]}
    contact: {required: [title]}

scheduler:
  checkin: {times: ["10:30", "15:00"]}

plugins:
  job_search:
    scheduler:
      run: {time: "06:30"}
    roles: ["Project Coordinator", "Program Coordinator", "!Intern"]
    locations:
      home_city: Vancouver, BC
      remote: true
      countries: {US: [], CA: [Vancouver, Victoria]}
    scraper:
      enabled: true
    enrichment:
      criteria:
        - label: Sponsorship
          assess: Does the role offer visa sponsorship?
    sources:
      greenhouse:
        enabled: true
        greenhouse_boards: [company-a, company-b, company-c]
```
