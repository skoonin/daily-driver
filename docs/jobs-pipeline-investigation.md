# Jobs Discovery Pipeline Investigation

**Date**: 2026-04-16
**Scope**: `make gather-jobs`, `make backfill-jobs`, `scripts/scrape-jobs.py`, `scripts/gather-jobs.sh`
**Goal**: Document how the pipeline works, why enrichment silently drops data, why `backfill-jobs` is needed on fresh scrapes, where the Notes column is written, and where the structure is holding us back. No code changes made.

---

## 1. Pipeline Flow: `make gather-jobs` End-to-End

Entry point: `Makefile:248` -> `bash scripts/gather-jobs.sh`.

### Phase 1 -- Queue File Generation (`scripts/gather-jobs.sh`)

Pure Bash URL-generator. Steps:

1. **Weekend guard** (`gather-jobs.sh:27-30`): exits 0 on Sat/Sun -- no scraping on weekends.
2. **Reads config** via `yq`: enabled boards, enabled companies, `roles[]`.
3. **Generates `job-queue-{TODAY}.md`**: one clickable search URL per (board x role) combination. Deletes previous days' queue files (`gather-jobs.sh:35`). Human-review artifact only -- the Python scraper does not read it.

### Phase 2 -- Python Scraper (`scripts/scrape-jobs.py`)

`gather-jobs.sh:222` invokes `python3 scripts/scrape-jobs.py --config config.yaml`, tee'ing combined stdout/stderr to `logs/gather-jobs.log`. `set +e` wraps the call (`gather-jobs.sh:221`), so scraper failure does **not** propagate as a non-zero exit from the shell script.

`main()` at `scrape-jobs.py:2096-2208` sequences:

1. **Load/init CSV** (`load_existing_jobs`, `2138-2150`): reads `{output_dir}/jobs.csv`, builds `known_urls` (Link column) and `known_keys` (dedup_key(company, role)).
2. **Run all scrapers** (`run_all_scrapers`, `2157`):
   - Phase 1 (headless, parallel via `ThreadPoolExecutor`, default 4 workers): `remoteok`, `weworkremotely`, `hn_who_is_hiring`, `greenhouse`, `apple`.
   - Phase 2 (visible Chromium, serial): `linkedin`, `indeed`, `wellfound`.
3. **Cross-run dedup** (`2160-2170`): drop URLs already in `known_urls` or (company, role) in `known_keys`. Drop URL-less jobs.
4. **Location filter** (`2174-2177`): `location_matches()` -- applied after dedup, before enrichment.
5. **Detail-page enrichment** (`enrich_job_details`, `2183`): fetches each job's URL to extract comp, posted_date, description_text via JSON-LD or source-specific HTML parsers.
6. **Company enrichment** (`enrich_company_descriptions`, `2184`): one `claude` CLI call per unique company -- fills Product/Purpose and GD Rating.
7. **Fit scoring** (`enrich_fit`, `2185`): one `claude` CLI call per job -> Fit 1-10.
8. **Notes generation** (`enrich_notes`, `2186`): one `claude` CLI call per job -> one-line summary.
9. **Append to CSV** (`append_jobs`, `2197`).
10. **Notification** (`2204-2205`): macOS notification if any jobs were appended.

Output: `{config.output_dir}/jobs.csv`. Logs: `logs/gather-jobs.log` (append).

---

## 2. Why `make backfill-jobs` Exists -- Silent Enrichment Failure Paths

`backfill-jobs` (`Makefile:254`) runs `scrape-jobs.py --backfill` -> `backfill()` at `scrape-jobs.py:2040-2091`. It scans existing rows, finds empty Product / Fit / GD Rating / Notes, and re-runs all three enrichers with `budget=sys.maxsize`. It exists because enrichment silently fails or is skipped in many places during a normal run:

### A. Budget Caps Silently Skip Jobs

All three enrichers default to a budget of 50 (`max_enrich_companies`, `max_enrich_fit`, `max_enrich_notes`):

- `enrich_company_descriptions` (`scrape-jobs.py:272, 293`): exhausted budget -> cache entry becomes `{"product": "", "gd_rating": ""}`.
- `enrich_fit` (`scrape-jobs.py:372, 383`): log warning and `break` -- remaining jobs get no Fit.
- `enrich_notes` (`scrape-jobs.py:436, 446`): same pattern.

If a single run yields > 50 new jobs, enrichment stops mid-list with only a warning. No retry, no marker on the row.

### B. Claude CLI Timeouts Produce Empty Fields

`enrich_timeout` default is 30s. Timeouts are caught and the field is left empty with a warning:

- `enrich_company_descriptions`: `scrape-jobs.py:327-329` -> empty cache entry.
- `enrich_fit`: `scrape-jobs.py:413-414` -> field untouched.
- `enrich_notes`: `scrape-jobs.py:482-483` -> field untouched.

### C. Non-zero Claude Exit Code Silently Dropped

- `enrich_company_descriptions`: `scrape-jobs.py:325-326` writes empty cache entry on failure.
- `enrich_fit`: `scrape-jobs.py:399-416` has no `else` branch at all for non-zero returncode -- job dict never updated.
- `enrich_notes`: `scrape-jobs.py:479` guards on `returncode == 0 and stdout.strip()`, silently skipping otherwise.

Rate limits, auth errors, transient network failures all fall into these branches.

### D. `claude` CLI Not on PATH

All three enrichers bail immediately if `shutil.which("claude") is None` (`scrape-jobs.py:256, 362, 426`) with a debug-level log. Under launchd, PATH often excludes the Claude install location. This is the single most likely cause of mass-empty-field batches.

### E. OSError Logged at DEBUG Only

Process-spawn errors are caught as `OSError` and logged via `log.debug(...)` in all three enrichers (`scrape-jobs.py:330-332, 415-416, 483-484`). Default log level is INFO -- these are invisible.

### F. Parallel Scraper Failures Don't Halt the Run

`_run_one()` (`scrape-jobs.py:1865-1887`) catches all exceptions and returns them; `_merge_and_dedup()` (`1906`) converts them to `failed_sources` entries. A failed source produces zero rows, the run continues, and the only trace is a WARNING/ERROR log line.

### G. Concurrent CSV Append Race (Not Enrichment, But Same Class of Silent Data Loss)

`append_jobs()` opens `jobs.csv` in append mode at `scrape-jobs.py:946` (`open(csv_path, "a", newline="", encoding="utf-8")`) with **no `fcntl.flock` or other file lock**. If a scheduled launchd run overlaps with a manual `make gather-jobs`, the two writers will interleave rows at the OS level, producing malformed CSV with mixed partial rows. More common in practice than the non-atomic backfill rewrite (§6), because the append path runs every day. A simple `fcntl.flock(LOCK_EX)` around the write or a lockfile sentinel would prevent this.

### H. Imprecise Budget-Warning Behavior Across Enrichers

Budget-cap behavior is similar but not identical across the three enrichers:

- `enrich_fit` and `enrich_notes` log one warning and `break` (`:382-383, :443-446`). One warning per run, at most.
- `enrich_company_descriptions` iterates a dedup'd company cache and uses a `budget_warned` flag (`:274, :285`) to suppress duplicate warnings across cache-miss re-entries. Same data outcome, different logging cadence.

Not a correctness bug, but relevant when auditing logs for missed enrichments.

### Bonus: Latent Docstring Bug in `enrich_fit`

`scrape-jobs.py:359` docstring claims `max_enrich_fit` default is 5. Actual code at `:372` uses `int(cfg.get("max_enrich_fit", 50))`. Off by 10x. Worth fixing alongside the logging promotions in the recommendations.

---

## 3. Notes Column -- All Write Paths

Three paths can write to the `Notes` column:

### Path 1: Scrape -> `enrich_notes()` -> `append_jobs()`

`enrich_notes()` (`scrape-jobs.py:420-485`) mutates `job["notes"]` in-place. `append_jobs()` (`scrape-jobs.py:963`) writes `row["Notes"] = job.get("notes", "")`. **Primary write path during `gather-jobs`.**

### Path 2: Backfill -> `enrich_notes()` -> `_dict_to_row()` -> full CSV rewrite

`backfill()` (`scrape-jobs.py:2069`) calls `enrich_notes(jobs, config, budget=sys.maxsize)`, then rewrites the CSV via `_dict_to_row()` (`2082`), mapping `job["notes"]` via `_DICT_TO_CSV` (`2015`) into the `Notes` column.

### Path 3: `append_jobs()` passthrough

`append_jobs()` (`scrape-jobs.py:963`) blindly copies whatever is in `job.get("notes", "")`. No scraper currently sets this directly, but any future scraper that did would bypass enrichment entirely.

### To Stop Populating Notes

- Set `enrich_notes: false` under `job_search.scraper` in `config.yaml` -- `enrich_notes()` gates on this at `scrape-jobs.py:433`.
- Belt-and-braces: `max_enrich_notes: 0`.
- Note: `backfill()` calls `enrich_notes()` unconditionally at `scrape-jobs.py:2069`, but the inner flag check at `:431-433` early-returns correctly, so the config flag fully disables backfill notes enrichment. The call site is not self-documenting, but behavior is correct.
- Path 3 (scrapers writing `notes` directly) is not currently used but would bypass the flag. Worth a guard in `append_jobs()` if Notes is permanently retired.

---

## 4. Class/Structure Assessment

`scrape-jobs.py` is 2209 lines of procedural Python, grouped only by comment banners:

| Lines       | Section                                                |
|-------------|--------------------------------------------------------|
| 1-111       | Docstring, imports, `COUNTRY_MAP`, `COUNTRY_NAMES`     |
| 113-196     | Config accessors                                       |
| 199-229     | `location_matches()`                                   |
| 232-242     | Dedup helpers                                          |
| 245-485     | Three enrichers                                        |
| 488-862     | Detail-page parsing, comp formatters, HTML parsers     |
| 865-969     | CSV helpers                                            |
| 972-1084    | Role-matching, search-term compression                 |
| 1087-1157   | Shared HTTP / Playwright helpers                       |
| 1159-1831   | Eight scraper functions                                |
| 1834-1973   | `SCRAPERS` dict, `_run_one`, `_merge_and_dedup`, orchestrator |
| 1976-2091   | Notification + `backfill()`                            |
| 2094-2209   | `main()`                                               |

### Coupling Problems

1. Every function takes the raw `config: dict` -- implicit coupling on schema throughout.
2. Enrichers mutate job dicts in-place and return nothing. There is no way to distinguish "enriched successfully to empty" from "silently skipped" -- this is the root cause of `backfill-jobs` existing.
3. `main()` is a ~115-line sequential script (`scrape-jobs.py:2096-2209`) hard-wired to all stages -- stage-level unit testing requires full config + filesystem mocking.
4. `backfill()` is a second entry point that reimplements the enrichment sequence with different semantics (no detail fetch, no dedup, unconditional rewrite).
5. Substantial copy-paste across the three Playwright scrapers (`scrape_linkedin`, `scrape_indeed`, `scrape_wellfound`): browser lifecycle, navigation, wait logic, card parsing.

### What a Class Refactor Would Buy

- `Source` base class with `fetch() -> list[dict]`, per-source subclasses.
- `PlaywrightSource` mixin collapsing the three browser-scraper duplications.
- `Enricher` class with explicit `EnrichmentResult { enriched: bool, reason: str }` per job -- eliminates the silent-empty-field problem and removes the need for `backfill-jobs` to exist (or reduces it to a retry-on-failure tool).
- `CSVStore` owning header/append/backfill/migration.
- `Pipeline` orchestrator with explicit stage boundaries, per-stage exit status, and a run-summary record.

This is the structural change most likely to make "did the pipeline run to completion" answerable.

---

## 5. Filtering Efficiency

### Where Filtering Currently Happens

- **Role filter**: applied inside each scraper at card-parse time -- early and cheap. API scrapers filter before building the job dict. Playwright scrapers filter per card on the already-loaded page (no extra HTTP).
- **Location filter**: `main():2174-2177`, after all scrapers complete and after cross-run dedup, before enrichment.
- **Dedup**: within-run in `_merge_and_dedup()` (`scrape-jobs.py:1890-1919`); cross-run in `main()` (`2160-2163`). Both before enrichment.

Order is correct for the expensive stages: no detail-page fetch or Claude call happens for jobs that will be filtered out.

### Wasted Work Identified

1. **Playwright scrapers don't pre-filter on location**: LinkedIn iterates up to `max_pages x len(terms) x len(countries)` pages (roughly 3 x ~10 x 6 = ~180 page loads). Cards are role-filtered in-scraper but the location string visible on each card is ignored until `main()` re-filters. Indeed has the same pattern. Applying location filtering in the scraper (on the card-level location text) would drop clearly-wrong-location cards before they enter the returned list, shrinking dedup and enrichment input.
2. **No cross-source prefix dedup before detail fetch**: if two sources return the same (company, role) with slightly different URLs, both go through `enrich_job_details` before the (company, role) dedup key catches them later. This is a small effect -- the cross-run dedup runs before detail fetch in `main()` -- but within-run the ordering could be tightened.
3. **Enrichment loops are O(n) per budget check**: not a correctness issue, just noted.

Summary: filtering is mostly in the right place. The concrete efficiency win is **pushing location filtering into the Playwright scrapers** so LinkedIn/Indeed/Wellfound don't collect rows they'll discard.

---

## 6. Completion Guarantees, Partial Failure, Idempotency

### Exit Codes

- `gather-jobs.sh` runs under `set -euo pipefail` (`:2`) but wraps the Python call with `set +e` (`:221`) and captures `scraper_exit=${PIPESTATUS[0]}` (`:223`). Non-zero scraper exit is surfaced: a WARNING is printed (`:225-227`) and the macOS notification message changes to reflect failure (`:234-235`). However, the shell does NOT propagate the non-zero code -- `exit $scraper_exit` is never called, so launchd sees success.
- `scrape-jobs.py` exits 1 if any source failed (`:2202`), 0 otherwise -- visible in logs and notification, but not in launchd's view of the run.

### Partial Failure

- A failed source produces no rows; rest of the run continues. `failed_sources` is logged at WARNING (`:2181`) and ERROR (`:2201`).
- No checkpointing. If the process is killed mid-enrichment, partially enriched rows are never written -- rows are only appended after all enrichment stages complete (`:2197`).
- Budget-cap skips leave rows with empty fields with no marker distinguishing "not enriched yet" from "enriched to empty". Backfill detects this purely by checking emptiness.

### Logging

- Python `logging.basicConfig` at INFO (`:2103-2108`). All `log.debug()` calls in the enrichers are invisible at default level.
- Unstructured log format. No per-run summary record (start, end, sources ok/failed, counts).
- `logs/gather-jobs.log` is append-only; no rotation.

### Idempotency

- Re-runs are safe: `load_existing_jobs()` builds the dedup sets.
- CSV migration (`_migrate_legacy_header`, `:874-898`) backs up to `.csv.bak.*` before rewriting.
- Backfill rewrites the full CSV with a backup (`:2072-2073`). Not atomic -- no temp-file-plus-rename; a mid-write crash could corrupt the file.
- Weekend guard in `gather-jobs.sh:27-29` means Sat/Sun launchd runs silently succeed without touching anything.

### No Completion Signal

No `last-run-at` sentinel, no summary row, no checksum. The only signals are the macOS notification (`:2204-2205`) and the `"Scraper complete: N new jobs appended"` log line (`:2198`). Both are ephemeral.

---

## 7. Config vs Markdown Sourcing

All pipeline runtime behavior is driven exclusively from `config.yaml`. **No markdown files are read by the scraper or enrichers at runtime.** The `claude --bare --no-session-persistence -p ...` invocation (`scrape-jobs.py:402, 476`) explicitly disables session context. [UNVERIFIED] whether `--bare` also suppresses project-level `CLAUDE.md` auto-discovery depends on the Claude CLI implementation, not this repo -- worth confirming empirically before relying on it.

### config.yaml keys read by `scripts/scrape-jobs.py`

Accessors live at `scrape-jobs.py:116-184`.

**Top-level / `job_search.*`:**
- `output_dir` (`:122`)
- `job_search.persona` (`:161`) -- injected verbatim into the fit prompt
- `job_search.roles[]` (`:132`) -- role filter + URL search terms
- `job_search.domain_keywords[]` (`:171`) -- Tier 2 fuzzy title match
- `job_search.seniority_keywords[]` (`:176`) -- Tier 2 seniority gate

**`job_search.locations.*`:**
- `locations.home_city` (`:165`) -- prompt input for `enrich_notes` and `_location_summary`
- `locations.remote` (`:215`)
- `locations.countries[]` (`:181`) -- drives `COUNTRY_MAP` for Playwright scrapers
- `locations.cities[]` (`:220`) -- non-remote allowlist

**`job_search.scraper.*`:**
- `scraper.enabled` (`:2134`), `scraper.headless` (`:1137`), `scraper.parallel_workers` (`:1935`)
- `scraper.sources.{source_id}` (`:1936`) -- per-source enable
- `scraper.greenhouse_boards[]` (`:1376`)
- `scraper.hn_max_posts` (`:1270`)
- `scraper.max_pages` (`:1438, 1540, 1726`), `scraper.date_window_days` (`:1439, 1542`)
- `scraper.timeout` (`:144`), `scraper.enrich_timeout` (`:150`)
- `scraper.detail_delay_seconds` (`:589`), `scraper.user_agent` (`:137`)
- `scraper.search_terms` (`:1081`) -- optional override for compressed role terms
- `scraper.enrich_fit` / `scraper.max_enrich_fit` (`:367, 372`)
- `scraper.enrich_notes` / `scraper.max_enrich_notes` (`:431, 436`)
- `scraper.max_enrich_companies` / `scraper.enrich_gd_rating` (`:262, 263`)

**`scripts/gather-jobs.sh` (yq, `:48-222`):**
- `job_search.sources.boards[].enabled|.id|.name|.search_url|.static_url`
- `job_search.sources.companies[].enabled|.id|.name|.careers_url`
- `job_search.roles[]`

### What the enrichment prompts read

The Claude prompts are built from in-memory fields + config strings -- they never open a markdown file:

- `enrich_company_descriptions` (`:247`): `"What does {company} build or do?"` -- pure string.
- `enrich_fit` (`:356`): `role_persona` from config + `_location_summary()` from config.
- `enrich_notes` (`:420`): `home_city` from config + scraped `job["description_text"]`.

`context.md` and `voice-profile.md` are **not** plumbed into the scraper, even though they exist and are relevant to fit/notes judgment.

### Values hardcoded that probably belong in config

- `_WWR_RSS_CATEGORIES` (`:1200`): `["devops-sysadmin", "back-end-programming", "full-stack-programming"]`.
- `NON_HEADLESS_SOURCES` (`:1850`): `frozenset({"linkedin", "indeed", "wellfound"})` -- should derive from a `type: playwright` flag on the source.
- Playwright `wait_for_timeout()` delays (`:1451-1784`): country switch 5000ms, page load 3000ms, scroll 2000ms, Apple search 4000ms -- all hardcoded. A `scraper.playwright_delays` block would make tuning code-free.
- `_LINKEDIN_RANGE_RE` currency prefixes (`:643`): recognizes `CA$|US$|\$|£|€|[A-Z]{3}`; adding AUD, SGD, etc. requires a code change.

### Values in config that might belong in markdown

- `job_search.persona` is one line today. If fit-scoring ever needs the fuller user profile (must-haves, deal-breakers, career context), that prose belongs in `role-criteria.md` (or `context.md`) -- config is the wrong vehicle for paragraphs. Current short string is fine at current scope.
- `job_search.roles[]` with its `!`-exclusion syntax works, but reads poorly; a `roles.md` with front-matter + narrative could improve clarity. Not urgent.

---

## 8. Compensation Data Reliability & Filter Feasibility

### Per-source Comp extraction

| Source | Where Comp comes from | Reliability |
|---|---|---|
| LinkedIn (`:1428`, parser `:692`, normalizer `:669`) | `<div class="compensation__salary">` on detail page, three regex variants | Moderate -- only when LinkedIn discloses it (jurisdictional) |
| Greenhouse (`:1367`, parser `:729`) | Regex `Annual Salary:\s*<prefix><min>-<max>` on detail HTML -- known to match Anthropic's board; untested elsewhere | Low, board-specific |
| RemoteOK (`:1187`) | No scrape-time comp; relies on `parse_jsonld_jobposting`. API has no salary field | Low |
| WeWorkRemotely (`:1207`) | RSS has no salary; JSON-LD fallback | Low |
| Indeed (`:1530`) | No dedicated parser; JSON-LD fallback (UK/EU market-dependent) | Low-moderate |
| HN Who's Hiring (`:1263`) | Parser only reads `parts[0:2]` + location heuristic; comp in comment body is never captured | None |
| Apple (`:1708`) | API JSON has no salary; JSON-LD lacks `baseSalary` | None |
| Wellfound (`:1631`) | Card hardcodes `"location": "Remote"`; no comp selector even though Wellfound UI shows it | None today (extractable) |

### Output formats (Comp column as written)

`_format_comp` (`:509`) normalizes JSON-LD `MonetaryAmount` into human strings:

- Range: `$150,000–$200,000/yr` (en-dash, no spaces)
- Single: `$175,000/yr`
- CAD: `CA$130,000–CA$150,000/yr`
- EUR/GBP: `€80,000/yr`, `£90,000/yr`
- Unknown currency: `CHF 120,000/yr` (code prefix + space)

`_clean_linkedin_comp` matches this shape. The Greenhouse regex path (`:768`) is similar but assembles the max bound with its own prefix, so edge cases can yield `$150,000–USD 200,000/yr`.

**The column is human-readable but not machine-parseable.** There is no structured `{min, max, currency, period}` anywhere in memory or on disk.

### Filter feasibility today

A hard exclusion filter (`min_comp_usd` drops listings below N) would discard most rows because Comp is empty for the majority of sources. Only LinkedIn + select Greenhouse boards populate it with any consistency.

A **downgrade filter** (reduce Fit score when Comp is empty or below threshold) is feasible today without losing coverage.

### Recommended config keys

```yaml
job_search:
  scraper:
    min_comp_usd: 150000            # "" = no filter
    min_comp_mode: downgrade         # downgrade | exclude
    downgrade_missing_comp: true
    downgrade_missing_comp_by: 2     # fit-score points to subtract
    comp_currency_default: USD
    comp_fx:                         # static exchange rates used for filter math
      CAD: 0.73
      EUR: 1.08
      GBP: 1.27
```

### Recommended standardized Comp representation

Keep the human string in the `Comp` column (no break for existing rows) and add a normalization step that also computes machine-readable fields stored on the in-memory job dict:

- `comp_min_native`, `comp_max_native` (int)
- `comp_currency` (ISO 4217 string)
- `comp_period` (`year` | `hour` | `month`)
- `comp_min_usd` (int, derived via `comp_fx`)

`comp_min_usd` is what the filter uses. Write it to a new `Comp (USD min)` column for visibility, or keep it ephemeral and write only the display string. Pre-req for making comp filtering honest: **fix extraction for Wellfound (selector exists, trivially recoverable), audit Greenhouse regex against at least 3 boards, and confirm LinkedIn normalizer handles "per hour" and "per month" formats** (current code assumes `/yr`).

---

## 9. CSV Data Standardization

`append_jobs` (`:939`) is the single write gate for the 13-column `CANONICAL_HEADER` (`:867`). No normalization layer exists between scraper output and write -- each scraper's quirks land directly in the CSV.

### Column-by-column audit

| Column | Current state | Variation seen | Needs normalization? |
|---|---|---|---|
| Status | Hardcoded `"found"` at `:959` -- user-edited thereafter | None at write | No (see Status vocabulary below) |
| Notes | Claude output | Free-form | **User wants OFF** (see §3) |
| Company | Source-specific | Mostly clean | Minor -- trim, title-case |
| Location | Source-specific | `"Remote"`, `"Anywhere"`, `"USA Only"`, `"San Francisco, CA"`, `"London, England, United Kingdom"`, `"Multiple Locations"`, `""`, Wellfound hardcoded `"Remote"` (`:1693`), RemoteOK fallback to `"Remote"` (`:1190`) | **Yes -- high priority** |
| Role | Source-specific | Mostly clean | Minor -- strip trailing ` (Remote)` suffixes |
| Fit | `enrich_fit:410` writes `"{n}/10"` or `""` | Consistent | No |
| Comp | See §8 | `$150,000–$200,000/yr`, `CA$130k-$150k/yr`, `""`, possibly `$150,000–USD 200,000/yr` | **Yes -- high priority** (Q8) |
| Date Found | `date.today().isoformat()` everywhere | Consistent ISO 8601 | No |
| Date Applied | `""` at write; user-edited later | Unvalidated | Leave to user, but a schema check in `/day-end` would help |
| Link | URL string | Consistent | No |
| Product/Purpose | Claude output | Free-form prose | Length-cap only |
| GD Rating | Claude output from prompt asking for a number | Could be `"4.2"`, `"4.2/5"`, `"unknown"` | **Yes -- strip to bare float** |
| Source | Per-scraper string | `"RemoteOK"`, `"We Work Remotely"`, `"HN Who's Hiring"`, `"Greenhouse (anthropic)"` (board slug embedded at `:1416`), `"LinkedIn"`, `"Indeed"`, `"Wellfound"`, `"Apple Careers"` | **Yes -- stable canonical IDs; board slug to a separate column or dropped** |

### Status vocabulary (from `CLAUDE.md`)

Only `found` is written by the scraper. All other values are set manually (or by `tracker.sh` for rows that graduate into the application pipeline):

| Status | Meaning |
|---|---|
| `found` | Just discovered; scraper default |
| `researched` | Details reviewed, not yet applied -- **to be replaced by `pending`** |
| `pending` | **NEW** -- planned to apply, not yet submitted (replaces `researched`) |
| `applied` | Application submitted; `Date Applied` set; also mirrored into `tracker.yaml` |
| `screening` | In recruiter/phone screen stage |
| `interviewing` | Past screen, in interview loops |
| `offer` | Offer extended |
| `skipped` | Rejected by Shawn before applying; reason required in Notes (but Notes is being retired -- see §3; new home for skip reasons TBD) |
| `rejected` | Rejected by company |
| `ghosted` | No response after reasonable window |
| `dropped` | Stopped pursuing; re-activatable if contacted |
| `withdrawn` | Shawn withdrew from process |

**Action item:** add `pending` to the Status enum everywhere it's documented/validated and retire `researched`. Specific sites that need updating:

- `CLAUDE.md` Status values list in the "Jobs Table (`jobs.csv`)" section.
- `scripts/tracker.sh:406` (`csv_active` filter) -- currently matches `applied|screening|interviewing|offer|found|researched`. Must add `pending` and drop `researched`, otherwise the audit will silently classify pending rows as inactive.
- Any future `normalize_job` enum check.

`pending` conveys intent-to-apply more clearly than `researched`.

Standardization implication: `normalize_job()` should not touch Status at write time (scraper always sets `found`), but downstream tooling should validate Status values against this enum when rewriting rows. Retiring Notes removes the current home for `skipped` rationale -- consider a separate `skip_reason` column or moving rationale to the company doc.

### Insertion point for a `normalize_job()` step

`main()` at `scrape-jobs.py:2183-2186`:

```python
new_jobs = [j for j in all_jobs if j["url"] not in known_urls and ...]
enrich_company_descriptions(new_jobs, config)
enrich_fit(new_jobs, config)
enrich_notes(new_jobs, config)
# INSERT: new_jobs = [normalize_job(j, config) for j in new_jobs]
count = append_jobs(csv_path, new_jobs, header)
```

`backfill()` (`:2067-2069`) needs the same normalize pass after re-enrichment.

Responsibilities of `normalize_job()`:

1. **Location**: canonical vocabulary (`"Remote"`, `"Remote (US)"`, `"Remote (Canada)"`, `"{City}, {Region}, {Country-ISO}"`). Map `"Anywhere"`, `"Worldwide"`, `""` -> `"Remote"` when `locations.remote: true`. Strip redundant country suffixes.
2. **Comp**: parse display string -> attach `comp_min_usd` (per §8); rewrite display to a single consistent format.
3. **GD Rating**: regex out the number, format to 1 decimal place, `""` if unparseable.
4. **Source**: lookup table keyed by `source_id` -> canonical name; board slug moves to `company` or a new `board` column.
5. **Role**: strip parenthetical suffixes already encoded in Location (e.g. `" (Remote)"`).
6. **Company**: trim, normalize punctuation (e.g. `"Inc."` / `"Inc"` / `"Inc,"`).

Validation at write time (fail loud rather than silently write malformed rows) is a related opportunity -- currently `append_jobs` writes whatever dict it receives.

### Schema-migration blast radius (test suite coupling)

Any change that alters `CANONICAL_HEADER` (adding `Comp (USD min)`, splitting Source, adding `skip_reason`) must be applied atomically across:

- `scripts/scrape-jobs.py:867` -- `CANONICAL_HEADER` definition.
- `tests/fixtures.py:3-7` -- hardcoded 13-column header; test fixture depends on exact order.
- `tests/test_csv.py:179-187` -- `test_canonical_header_status_first_no_hash` asserts length == 13 and exact column order.
- `tests/test_csv.py:190-246` -- `_migrate_legacy_header` tests assert specific column positions.
- `tests/test_scrapers.py:401-407` -- phase-routing assertions pin which source goes to which phase; any new source added to `SCRAPERS` / `NON_HEADLESS_SOURCES` must be reflected here.
- `scripts/tracker.sh:406` -- `csv_active` status filter (see Status vocabulary action item above).
- `_row_to_dict` / `_dict_to_row` + `_DICT_TO_CSV` -- the existing partial normalization layer; new columns require mapping entries.

Treat header as a pinned interface: plan for a single migration PR per schema change, not incremental additions.

---

## Recommendations (for future planning -- not applied here)

Stack-ranked by cost. Do the quick fixes first; defer the structural work until the quick fixes don't solve the pain.

### Quick fixes (under 1 hour total)

- **Stop writing Notes**: `enrich_notes: false` + `max_enrich_notes: 0` in `config.yaml` (see §3).
- **Promote failure logs**: change `log.debug` -> `log.warning` for OSError and non-zero returncode branches in all three enrichers (`scrape-jobs.py:325-332, 399-416, 479-484`).
- **Propagate scraper exit**: add `exit $scraper_exit` at the end of `gather-jobs.sh` so launchd sees real failures.
- **Fix the docstring bug**: `enrich_fit` docstring at `scrape-jobs.py:359` says default 5; code uses 50.
- **Emit end-of-run summary line**: `"Fit enriched: X/Y, Z failed; Notes: ..."` before `append_jobs` -- surfaces budget exhaustion without code changes.

### Medium-cost (half-day each)

- **Per-job enrichment state**: stamp `_enrichment_state = "ok" | "failed" | "skipped"` on each job before append; write as hidden CSV column or keep in-memory. Backfill becomes "retry failed" instead of "retry empty." Obviates most of what `backfill-jobs` does today.
- **Add `fcntl.flock(LOCK_EX)`** around `append_jobs`'s file open at `scrape-jobs.py:946` AND around `backfill()`'s full-rewrite at `:2072-2073`. Locking only `append_jobs` is necessary but not sufficient: a concurrent `gather-jobs` append and `backfill-jobs` rewrite would still collide. Both write paths must share the same lock sentinel. `tracker.sh audit` (`:358-489`) is a read-only consumer that could also observe partial state mid-rewrite.
- **Run-summary JSON**: write `{output_dir}/jobs-last-run.json` with start, end, per-source status, counts. Makes "did it complete" answerable.
- **Push location filter into Playwright scrapers** (LinkedIn, Indeed, Wellfound) using card-level location text.

### Longer-term (days to weeks)

- **Class refactor**: `Source` + per-source subclasses, `Enricher` with explicit results, `CSVStore`, `Pipeline`. Only worth doing if the medium-cost items above don't resolve the silent-failure / completion-guarantee problems.
- **Comp filter**: implement only after fixing Wellfound comp extraction and auditing Greenhouse regex across multiple boards. Start with a single `comp_min_usd` key and downgrade-mode only; defer the full FX table + structured comp schema until coverage justifies it.
- **`normalize_job()`**: if pursued, start narrow -- GD Rating strip + Source canonical IDs are one-liners. Full 6-responsibility normalizer only if source-level cleanup proves insufficient.

### Legacy numbered list (for cross-reference)

1. **Stop writing Notes**: set `enrich_notes: false` and `max_enrich_notes: 0` in `config.yaml`. Consider making `backfill()` explicitly respect the flag rather than relying on the inner function check. If Notes is permanently retired, also guard `append_jobs()` against path 3.
2. **Make enrichment failures visible**: promote OSError and non-zero-returncode logs to WARNING; record per-job enrichment status on the row (e.g. a hidden `_enrichment_state` column) so backfill becomes "retry failed" instead of "re-try empty".
3. **Eliminate budget surprise**: remove the silent budget cap or log a single prominent summary line at the end ("15/73 jobs not enriched -- Fit budget exhausted").
4. **Propagate scraper exit code** from `gather-jobs.sh` so launchd sees real failures.
5. **Write a run-summary file** (JSON) with start/end time, per-source status, per-stage counts -- makes "did it complete" answerable.
6. **Class refactor**: `Source` + per-source subclasses, `Enricher` returning explicit results, `CSVStore`, `Pipeline` orchestrator. This is the structural fix that obviates `backfill-jobs` in its current form.
7. **Push location filtering into Playwright scrapers** (LinkedIn, Indeed, Wellfound) using the card-level location text.
