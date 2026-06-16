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
| `schedule` | object | empty | day-start / day-end scheduled times |
| `voice_profile` | object | empty | |
| `tracker` | object | — | **Required** |
| `gather` | object | empty | |
| `claude` | object | empty | |
| `ai` | object | empty | |
| `focus` | object | empty | |
| `plugins` | object | empty | |

## `daily_driver`

| Key | Type | Default |
|-----|------|---------|
| `output_dir` | string | `.` (relative to workspace root) |

Rotated `jobs.csv` backups land in `<output_dir>/backups/` with UTC ISO-8601 timestamps (e.g. `jobs.csv.bak.2026-05-13T05-45-00Z`). `doctor` flags accumulated backups there.

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
| `warn_unknown_status` | bool | `true` | Print a one-line stderr nudge when `tracker add`/`update` sets a status outside the category-aware recommended set and not already used elsewhere. Status spelling is normalized first (case-folded, underscores/spaces → hyphens), so `Ruled_Out` matches `ruled-out`. `job` category → `found, skipped, applied, interviewing, rejected, dropped, closed`. All other categories → `open, in-progress, blocked, done, ruled-out` plus any `extra_statuses`. Set `false` to silence. |
| `extra_statuses` | list[string] | `[]` | Extra status values added to the (non-`job`) recommended set; values here never trigger the `warn_unknown_status` nudge. |

`required` values are the flags accepted by `tracker add`: `title`, `link`, `note`, `next_action`, `due`, `status`, `tags`.

## `ai`

Routes the headless `summary` task to either the `claude` CLI or a local [Ollama](https://ollama.com) server, and holds the shared `claude:` / `ollama:` provider-connection blocks. Those provider blocks are also consulted by the job_search plugin's own enrichment routing (`plugins.job_search.enrichment.provider`) — enrichment routing itself lives with the plugin, not here. Interactive launchers (day-start, check-in, day-end) always use `claude`; the `ai` block does not affect them.

Default (omitting the block entirely): `claude` for summary. Existing workspaces using the old `ai.enrichment` block must move that routing under `plugins.job_search.enrichment` (see the migration note below) — `ai.enrichment` is now rejected.

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `summary.provider` | `claude` \| `ollama` | `claude` | Used by `summary --range` |
| `summary.model` | string or null | null | Provider-specific identifier |
| `claude.max_parallel` | int (≥1) | 4 | Worker threads for parallel enrichment when the active provider is claude. Applies to both `jobs run` and `jobs backfill`. Set to 1 to force serial. Kept modest — claude is rate-limited and runs one CLI subprocess per call, so wide fan-out invites throttling |
| `ollama.endpoint` | string | `http://localhost:11434` | Consulted only when a task is routed to ollama |
| `ollama.timeout` | int (seconds) | 60 | Per-request timeout for ollama |
| `ollama.max_parallel` | int (≥1) | 4 | Worker threads for parallel enrichment. Applies to both `jobs run` and `jobs backfill`. Set to 1 to force serial; mirrors Ollama's server-side `OLLAMA_NUM_PARALLEL`. See [ollama-setup.md](ollama-setup.md) for RAM caveats |

Model identifiers are provider-specific. For `claude`: `sonnet`, `opus`, `haiku`. For `ollama`: any pulled tag (e.g. `qwen2.5:14b`, `phi4`, `llama3.2:3b`). `null` lets each provider pick its own default.

`max_parallel` raises enrichment throughput only — it does not change the `plugins.job_search.enrichment.max_enrich_*` budgets (which still cap how many jobs get enriched) or the per-call timeout.

See [`docs/ollama-setup.md`](ollama-setup.md) for installation, tuning, and the `doctor` reachability check.

Example: route enrichment to a local model, keep summary on claude. Enrichment provider/model now live under the plugin; the shared `ollama:` connection block stays here:

```yaml
ai:
  ollama:
    endpoint: http://localhost:11434
    timeout: 90
plugins:
  job_search:
    enrichment:
      provider: ollama
      model: qwen2.5:14b
```

Migration from the pre-split config — the routing moved off `ai:` onto the plugin:

```yaml
# Before (rejected):
ai:
  enrichment:
    provider: ollama
    model: qwen2.5:14b

# After:
plugins:
  job_search:
    enrichment:
      provider: ollama
      model: qwen2.5:14b
```

## `scheduler`

Freeform dict passed to the Jinja launchd plist templates. Times are local wall-clock, 24-hour. Defaults (when `scheduler` is omitted): check-in at 11:00 and 15:00, jobs at 07:00.

## `schedule`

Scheduled times for `day-start` and `day-end` (HH:MM, 24-hour). When set, `scheduler install` creates launchd jobs for those commands. Also drives `is_late_day` evaluation in day-start prompts. **Quote times in YAML** — bare `HH:MM` is parsed as a base-60 integer by PyYAML.

| Key | Type | Default |
|-----|------|---------|
| `day_start` | string (HH:MM) or null | null |
| `day_end` | string (HH:MM) or null | null |

## `claude`

Controls `claude` CLI session behavior for interactive launchers.

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `resume_check_in` | bool | false | When true, `check-in` resumes the prior day-start session instead of starting fresh |

## `gather`

Controls what `daily-driver gather <kind>` reads from external sources.

### `gather.git`

| Key | Type | Default |
|-----|------|---------|
| `search_paths` | list[path] | `[]` |

Directories to scan recursively for git checkouts. Paths may be absolute or tilde-prefixed (e.g. `~/git`).

## `focus`

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `default_duration` | string | `25m` | Fallback for `focus on` when `--for` is omitted. Accepts `30m`, `2h`, `1h30m`, or bare minutes. |

## `plugins.job_search`

| Key | Type | Default |
|-----|------|---------|
| `persona` | string or null | null |
| `home_city` | string or null | null |
| `roles` | list[string] | `[]` |
| `domain_keywords` | list[string] | `[]` |
| `seniority_keywords` | list[string] | `[]` |
| `locations` | object or null | null |
| `scraper` | object | see `ScraperConfig` below |
| `enrichment` | object | see `EnrichmentConfig` below |
| `sources` | dict[string, object] | `{}` (keyed by source id) |

Compensation is display-only: the scraper writes whatever amount it finds to the
`Comp` column and never filters on it. Location (countries) is the only
filter that removes jobs. Seniority filtering is driven by `seniority_keywords`.

### `locations`

| Key | Type | Default |
|-----|------|---------|
| `home_city` | string or null | null |
| `remote` | bool | false |
| `countries` | list[string] | `[]` |

### `scraper` (`ScraperConfig`)

Transport / retry knobs shared by every source.

| Key | Type | Default |
|-----|------|---------|
| `enabled` | bool | false |
| `max_retries` | int | 3 |
| `max_age_days` | int | 30 |
| `user_agent` | string | Firefox/128 UA |
| `timeout` | int | 30 |
| `search_terms` | list[string] or null | null |
| `parallel_workers` | int | 8 |
| `max_pages` | int | 3 |
| `browser` | `firefox` \| `chromium` \| `webkit` | firefox |

`headless` is not user-configurable. The field exists on the model but is overridden per scrape phase, so a value set in `.dd-config.yaml` has no effect; it is intentionally omitted from the generated config scaffold.

`browser` selects the Playwright engine for browser-driven sources (Apple). The
chosen engine must be installed first (`playwright install <engine>`); `doctor`
checks for the configured engine and `doctor --fix` installs it.

### `enrichment` (`EnrichmentConfig`)

Sibling block of `scraper` under `job_search`. Knobs for the post-scrape enrichment passes (comp, product/Glassdoor, fit, notes), plus the AI provider routing for those passes. The provider/model live here (enrichment is plugin-specific); the shared connection/tuning lives in the core `ai.claude:` / `ai.ollama:` blocks.

The fit/notes pass also reads `context.md` from the workspace root, if present, and injects it into every fit evaluation — so the fit score weighs how well your actual experience matches the role, and the location-fit and notes reflect your real preferences. Without a `context.md`, fit falls back to scoring on role/company/location alone. Because the file rides every per-job call, `jobs run` logs a one-line token-cost estimate when `context.md` is large.

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `provider` | `claude` \| `ollama` | `claude` | Which backend runs the enrichment LLM calls |
| `model` | string or null | null | Provider-specific identifier (e.g. `sonnet`, `qwen2.5:14b`) |
| `enrich_timeout` | int | 30 | |
| `max_enrich_companies` | int | 50 | |
| `enrich_product` | bool | true | Fill the Product/Purpose one-liner per company |
| `enrich_gd_rating` | bool | true | Fill the Glassdoor rating per company |
| `enrich_fit` | bool | true | |
| `enrich_notes` | bool | true | |
| `enrich_is_remote` | bool | true | Judge each job `remote`/`hybrid`/`onsite` during the fit/notes pass (no extra LLM call) |
| `max_enrich_fit` | int | 50 | |
| `detail_delay_seconds` | float | 0.5 | |
| `criteria` | list of `{label, assess}` | `[]` | |

`enrich_product` and `enrich_gd_rating` share one LLM call per company. With both off, the company pass is skipped entirely; with only one on, the prompt asks for and writes only that field.

#### `criteria` — the criteria scanner

A list of extra things to assess in each job description. The scanner rides
the existing fit/notes enrichment call (no additional LLM requests): each
criterion adds one instruction to that prompt, and a meaningful answer is
appended to the job's **Notes** column as a `Label: value` segment.

| Key | Type | Notes |
|-----|------|-------|
| `label` | string | Column-style prefix shown in Notes (e.g. `Sponsorship`) |
| `assess` | string | Natural-language instruction the LLM reasons about, not a keyword match — so negation and polarity survive ("Does the role offer visa sponsorship?") |

The scanner is quiet by default: when the description is silent on a
criterion, the LLM answers `unknown` and nothing is appended. So a job with
`notes: "Kubernetes shop"` and a meaningful sponsorship answer becomes
`Kubernetes shop | Sponsorship: Yes, H-1B`; a job that says nothing about
sponsorship keeps `Kubernetes shop` unchanged.

```yaml
enrichment:
  criteria:
    - label: Sponsorship
      assess: Does the role offer visa sponsorship?
    - label: Clearance
      assess: Is a US security clearance required?
```

### `sources` (`dict[str, SourceToggle]`)

Sibling block of `scraper` under `job_search`. A dict whose keys are source identifiers and whose values are per-source toggles. Bool values coerce to `{enabled: <bool>}`. Per-source knobs live on the matching `SourceToggle` subclass, so each source owns its own configuration:

| Source key | Toggle | Per-source knob (default) |
|-----------|--------|---------------------------|
| `weworkremotely` | `WeWorkRemotelyToggle` | `wwr_categories` (`[]`) |
| `greenhouse` | `GreenhouseToggle` | `greenhouse_boards` (`[anthropic]`) |
| `hn_who_is_hiring`, `hn_jobs` | `HackerNewsToggle` | `hn_max_posts` (`500`) |
| `linkedin` | `LinkedInToggle` | `results_wanted_per_query` (`50`), `hours_old` (`168`) |
| `indeed` | `IndeedToggle` | `results_wanted_per_query` (`50`), `hours_old` (`168`), `country` (`USA`) |
| any other | `SourceToggle` | (enable/disable only) |

`linkedin` and `indeed` are the two site-named sources fetched via the
`python-jobspy` library (an implementation detail). Their query knobs:

| Key | Type | Default | On |
|-----|------|---------|----|
| `results_wanted_per_query` | int | 50 | both |
| `hours_old` | int | 168 (7 days) | both |
| `country` | string | `USA` | indeed only |

`country` sets Indeed's regional host and is the fallback used when a configured
country code is not one the scraper recognizes. LinkedIn takes no country
parameter, so it has no `country` knob.

```yaml
sources:
  weworkremotely:
    enabled: true
    wwr_categories: [devops, sysadmin]
  greenhouse:
    enabled: true
    greenhouse_boards: [anthropic, stripe]
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

When `linkedin` and `indeed` are both enabled, each is fetched separately, under
its own progress row, with its own retry and failure isolation — so a slow or
failing LinkedIn scrape never blocks or fails the fast Indeed one. The live
progress row and the `Source` column are site-named. (Glassdoor and Google for
Jobs are excluded: broken or unsupported upstream.)

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
    seniority_keywords: [senior, staff, principal]
    locations:
      home_city: Vancouver, BC
      remote: true
      countries: [US, CA]
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
        greenhouse_boards: [anthropic, stripe, figma]
```

## Customization

### `.claude/` ownership

Generation only touches three paths. Everything else is yours.

| Path | Owner | Behavior on generate |
| --- | --- | --- |
| `.claude/commands/daily-driver/` | Daily Driver | Wiped and recopied from package |
| `.claude/agents/daily-driver/` | Daily Driver | Wiped and recopied from package |
| `.claude/settings.local.json` | Daily Driver + user merge | Re-rendered from template; user-added top-level keys preserved |
| `.claude/commands/*.md` (top level, outside `daily-driver/`) | You | Untouched |
| `.claude/agents/*.md` (top level, outside `daily-driver/`) | You | Untouched |
| Anything else under `.claude/` | You | Untouched |

User edits to files under `.claude/*/daily-driver/` are detected via SHA-256 manifest and preserved by `doctor --fix` on version drift. `doctor --reset` force-overwrites managed files regardless of edits.

### Custom Claude commands

Place a markdown file at `.claude/commands/my-command.md` (any path outside `daily-driver/`). Invoke with `/my-command` inside any `claude` session launched with `--add-dir <workspace>`.

### Custom Claude agents

Place a markdown file at `.claude/agents/my-reviewer.md`. Invoke with `claude --agent my-reviewer ...`.

### Override a shipped command

Do not edit a file under `.claude/commands/daily-driver/` — your edit will survive `--fix` but be lost on `--reset`. Instead, copy the file up one level:

```bash
cp .claude/commands/daily-driver/day-start.md .claude/commands/day-start.md
```

Edit the top-level copy. Claude Code resolves top-level before namespaced, so your version wins.

### Custom tracker categories

Categories are config-driven; no code changes needed. Edit `.dd-config.yaml`:

```yaml
tracker:
  default_category: task
  categories:
    task:    {required: [title]}
    ticket:  {required: [title]}
    contact: {required: [title]}
```

Any fields passed via `tracker add --extra key=value` land in the entry's `extras:` block without schema changes.

### Context and voice profile

`context.md` and `voice-profile.md` in the workspace root are copied once on `init` and are yours thereafter — edit freely. They are injected into Claude sessions launched by `day-start`, `check-in`, `day-end`, and `summary`. `voice-update --from PATH` rewrites `voice-profile.md` from writing samples via headless `claude`.

## See also

- [usage.md](usage.md) — the daily flow that exercises these settings.
- [ollama-setup.md](ollama-setup.md) — local-LLM provider walkthrough.
- [commands.md](commands.md) — subcommand flag reference.
- [dev/extending.md](dev/extending.md) — adding scraper sources or new subcommands.
