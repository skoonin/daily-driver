# Configuration

All workspace configuration lives in `.dd-config.yaml` at the workspace root. Validated by Pydantic on every command invocation; `extra="forbid"` on every model means unknown keys fail loudly.

Authoritative schema: `source/daily_driver/core/config_models.py`.

> For the end-user flow that exercises these settings, see [usage.md](usage.md). For Ollama provider setup, see [ollama-setup.md](ollama-setup.md).

## Top-level keys

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `daily_driver` | object | `{output_dir: "."}` | |
| `user_profile` | object | empty | |
| `recurring_tasks` | list | `[]` | |
| `scheduler` | object or null | null | Freeform; passed to launchd templates |
| `voice_profile` | object | empty | |
| `tracker` | object | — | **Required** |
| `plugins` | object | empty | |

## `daily_driver`

| Key | Type | Default |
|-----|------|---------|
| `output_dir` | string | `.` (relative to workspace root) |

## `user_profile`

Injected into Claude sessions as context.

| Key | Type | Default |
|-----|------|---------|
| `name` | string or null | null |
| `timezone` | string or null | null (IANA, e.g. `America/Vancouver`) |
| `citizenship` | list[string] | `[]` |
| `work_auth` | dict[string, string] | `{}` |
| `seeking_since` | date (YYYY-MM-DD) or null | null |

## `recurring_tasks[]`

| Key | Type | Required | Notes |
|-----|------|----------|-------|
| `name` | string | yes | |
| `cadence` | `daily`\|`weekly`\|`monthly` | yes | |
| `estimated_minutes` | int or null | no | |
| `day` | string or null | no | Only valid with `cadence: weekly` |

## `voice_profile`

| Key | Type | Values |
|-----|------|--------|
| `formality` | string or null | `formal`, `professional-casual`, `casual` |
| `sentence_length` | string or null | `short`, `medium`, `long` |
| `avoid_words` | list[string] | |
| `preferred_signoff` | string or null | |

## `tracker` (required)

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `default_category` | string | `task` | Must be a key in `categories` |
| `categories` | dict[string, object] | `{}` | Each category: `{required: [field, ...]}` |
| `warn_unknown_status` | bool | `true` | Print a one-line stderr nudge when `tracker add`/`update` sets a status outside the recommended set (`open`, `in-progress`, `blocked`, `done`, `ruled-out`) and not already used elsewhere. Set `false` to silence. |

`required` values are the flags accepted by `tracker add`: `title`, `link`, `note`, `next_action`, `due`, `status`, `tags`.

## `ai`

Routes headless AI tasks (enrichment, summary) to either the `claude` CLI
or a local [Ollama](https://ollama.com) server. Interactive launchers
(day-start, check-in, day-end) always use `claude`; the `ai` block does
not affect them.

Default (omitting the block entirely): `claude` for every task. Existing
workspaces need no migration.

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enrichment.provider` | `claude` \| `ollama` | `claude` | Used by `jobs run --backfill` |
| `enrichment.model` | string or null | null | Provider-specific identifier |
| `summary.provider` | `claude` \| `ollama` | `claude` | Used by `summary --range` |
| `summary.model` | string or null | null | Provider-specific identifier |
| `ollama.endpoint` | string | `http://localhost:11434` | Consulted only when a task is routed to ollama |
| `ollama.timeout` | int (seconds) | 60 | Per-request timeout for ollama |

Model identifiers are provider-specific. For `claude`: `sonnet`, `opus`,
`haiku`. For `ollama`: any pulled tag (e.g. `qwen2.5:14b`, `phi4`,
`llama3.2:3b`). `null` lets each provider pick its own default.

See [`docs/ollama-setup.md`](ollama-setup.md) for installation and the
`doctor` reachability check.

Example: route enrichment to a local model, keep summary on claude:

```yaml
ai:
  enrichment:
    provider: ollama
    model: qwen2.5:14b
  ollama:
    endpoint: http://localhost:11434
    timeout: 90
```

## `scheduler`

Freeform dict passed to the Jinja launchd plist templates. Times are local wall-clock, 24-hour. Defaults (when `scheduler` is omitted): check-in at 11:00 and 15:00, jobs at 07:00.

## `plugins.job_search`

| Key | Type | Default |
|-----|------|---------|
| `persona` | string or null | null |
| `locations` | object or null | null |
| `compensation` | object or null | null |
| `role_filters` | object or null | null |
| `roles` | list[string] | `[]` |
| `domain_keywords` | list[string] | `[]` |
| `seniority_keywords` | list[string] | `[]` |
| `min_comp_usd` | int | 180000 |
| `scraper` | object | see `ScraperConfig` below |
| `sources` | dict | `{}` (freeform, keyed by source id) |

### `locations`

| Key | Type | Default |
|-----|------|---------|
| `home_city` | string or null | null |
| `remote` | bool | false |
| `countries` | list[string] | `[]` |
| `cities` | list[string] | `[]` |

### `compensation`

| Key | Type | Notes |
|-----|------|-------|
| `currency` | `USD`\|`CAD`\|`EUR`\|`GBP` | Default `USD` |
| `minimum` | int | Required; floor |
| `target` | int | Required |
| `current` | int or null | |
| `maximum` | int or null | |

### `role_filters`

| Key | Type | Default |
|-----|------|---------|
| `levels` | list[string] | `[]` (e.g. `[senior, staff, principal]`) |
| `exclude_management` | bool | false |
| `exclude_keywords` | list[string] | `[]` |

### `scraper` (`ScraperConfig`)

| Key | Type | Default |
|-----|------|---------|
| `enabled` | bool | false |
| `user_agent` | string | Chrome/124 UA |
| `timeout` | int | 30 |
| `enrich_timeout` | int | 30 |
| `max_enrich_companies` | int | 50 |
| `enrich_gd_rating` | bool | true |
| `enrich_fit` | bool | true |
| `enrich_notes` | bool | true |
| `max_enrich_fit` | int | 50 |
| `detail_delay_seconds` | float | 0.5 |
| `search_terms` | list[string] or null | null |
| `headless` | bool | false |
| `wwr_categories` | list[string] | `[]` |
| `hn_max_posts` | int | 100 |
| `greenhouse_boards` | list[string] | `[anthropic]` |
| `jobspy` | object | `{results_wanted_per_query: 50, hours_old: 168, country_indeed: USA}` |
| `playwright_delays` | dict | `{}` |
| `sources` | dict | `{}` |
| `parallel_workers` | int | 4 |
| `max_pages` | int | 3 |

### `sources`

Freeform dict. Each key is a source identifier; values are passed to the scraper adapter. Marking a source with `type: playwright` routes it to the serial Playwright phase. See [extending.md](extending.md) for adapter details.

## Example

Fuller example for a job-search workspace:

```yaml
daily_driver:
  output_dir: output

user_profile:
  name: Alex Smith
  timezone: America/Vancouver
  citizenship: [US, CA]
  work_auth: {US: citizen, CA: citizen}
  seeking_since: 2026-04-01

voice_profile:
  formality: professional-casual
  sentence_length: medium
  avoid_words: [utilize, leverage, synergy]
  preferred_signoff: "Best,"

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
    persona: "Senior SRE, IC-track only"
    locations:
      home_city: Vancouver, BC
      remote: true
      countries: [US, CA]
    compensation:
      currency: USD
      minimum: 160000
      target: 185000
    role_filters:
      levels: [senior, staff, principal]
      exclude_management: true
      exclude_keywords: [manager, director, VP]
    scraper:
      enabled: true
      greenhouse_boards: [anthropic, stripe, figma]
    sources:
      linkedin:
        type: playwright
        search_terms: ["site reliability engineer"]
        pages: 3
```

## See also

- [usage.md](usage.md) — the daily flow that exercises these settings.
- [ollama-setup.md](ollama-setup.md) — local-LLM provider walkthrough.
- [commands.md](commands.md) — subcommand flag reference.
