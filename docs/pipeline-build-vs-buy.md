# Job-Search Pipeline: Build vs. Buy Research

**Date**: 2026-04-16
**Scope**: Evaluate existing open-source and commercial software that could replace parts of the custom `scrape-jobs.py` pipeline. Goal: avoid reinventing the wheel where good tooling exists.
**Context**: Personal job-search tool for one engineer (SRE/Platform IC, Vancouver BC, dual US/CAD). Local-first, open-source preference. Claude Code is primary agent environment.

---

## A. Multi-Source Job Scrapers (Open-Source)

### [JobSpy](https://github.com/speedyapply/JobSpy) -- top candidate
- MIT | ~3,200 stars | Last release v1.1.79 (March 21, 2025) | Python 3.10+
- Sources: LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter, Bayt, Naukri, BDJobs.
- **Comp extraction**: first-class -- returns `min_amount`, `max_amount`, `interval`, `currency`, `salary_source` (direct-data vs. description-parsed). Best-in-class FOSS salary extractor.
- Strengths: actively maintained (58 releases, 353 commits), no-auth Indeed, concurrent multi-board search, clean Pandas output, proxy support.
- Gaps: LinkedIn rate-limits ~page 10. Does NOT cover WWR, HN, Greenhouse board API, Apple, or Wellfound.

### [JobFunnel](https://github.com/PaulMcInnis/JobFunnel) -- abandon
- MIT | ARCHIVED December 2025. Bot detection killed it. Do not use.

### [py-linkedin-jobs-scraper](https://github.com/spinlud/py-linkedin-jobs-scraper) -- narrow
- MIT | 458 stars | LinkedIn only. README notes "anonymous session no longer maintained." No salary extraction.

### [JobOps](https://github.com/DaKheera47/job-ops) -- full pipeline alternative
- AGPLv3 + Commons Clause | ~2,700 stars | v0.3.1 (April 2026) | TypeScript
- Integrated: scraping (LinkedIn, Indeed, Glassdoor, Adzuna, Hiring Cafe, startup.jobs, Working Nomads, Gradcracker) + LLM fit scoring (0-100) + resume tailoring + application tracking + Gmail integration. Docker-compose deploy.
- LLM backends: Ollama, OpenAI, Gemini, OpenRouter.
- Gaps: TypeScript stack (not Python); missing HN, WWR, Apple, Greenhouse; no daily workflow / check-in commands; no contacts log; Commons Clause restricts commercial hosting.

**Recommendation**: JobSpy for LinkedIn/Indeed/Glassdoor/Google. Keep custom for HN, WWR, Greenhouse, Apple, Wellfound (niche, stable, or no alternative).

---

## B. Job Aggregator APIs

### [Adzuna API](https://developer.adzuna.com/) -- top free pick
- 12+ countries (strong CA/US/UK coverage). Free tier: 250 req/day, 25 req/min.
- Data: title, company, location, description, **salary_min/salary_max**, category, contract_type, created. Salary histogram + historical endpoints are genuine differentiators.
- Gaps: No HN, Wellfound, Greenhouse, Apple.

### [RemoteOK Public API](https://remoteok.com/api) -- free, no auth
- Free, attribution required. salary_min/salary_max parsed from listing text. Cleanest free salary field.
- Gaps: Remote-only, single source.

### [JSearch (RapidAPI)](https://www.openwebninja.com/api/jsearch)
- Google for Jobs aggregation. Free: 200 req/month (too tight for daily). Pro $25/mo (10k req). Salary only via separate Salary Estimate endpoint (Glassdoor/ZipRecruiter estimates).

### [SerpApi Google Jobs](https://serpapi.com/google-jobs-api)
- Free: 250 searches/mo. Starter $25/mo (1,000). Salary inconsistent (only what Google surfaces in extensions).

### [Jobicy API/RSS](https://jobicy.com/jobs-rss-feed)
- Free. 100 listings per call, once/hour cap. Remote-focused. Salary when posted.

### [TheirStack](https://theirstack.com/en/job-posting-api)
- 186M jobs from 325k+ sources. Normalized salary (strongest of any API). Free tier is minimal (50 company + 200 API credits/mo); enterprise-priced above.

### [Apify Actors](https://apify.com/store/job-scraper)
- Per-actor rentals: LinkedIn $19.99/mo + $0.02/50 results; Indeed $20/mo + $0.73/1k jobs. Not truly self-hosted.

### LinkedIn Jobs API
- **Does not exist publicly.** LinkedIn shut down the third-party Jobs API in 2015. Enterprise Talent Solutions only.

**Recommendation**: Free-only. Adzuna + RemoteOK API. **All paid options (JSearch Pro, SerpApi, TheirStack, Apify) are out of scope** per no-paid-software constraint.

See §Z below for expanded aggregator API research (in progress).

---

## C. Self-Hosted Application Trackers

### [JobSync](https://github.com/Gsync/jobsync)
- MIT | ~518 stars | v1.1.10 (April 2026) | Next.js, Docker
- Features: tracker + dashboard + resume mgmt + AI assistant (Ollama, OpenAI, Gemini). Young project.
- Gaps: No scraping, no contacts log, web UI only (no CLI), no daily workflow hooks.

### [JobOps](https://github.com/DaKheera47/job-ops) (tracker component)
- Integrated notes/status/resume per job. Not standalone.

### SaaS-only (all cloud, data-lock-in)
- **Huntr** $10/mo. **Teal** $29/mo. **Careerflow** $14/mo. **Simplify** free+. None self-hosted.

### Adaptable generic Kanban
- [Vikunja](https://vikunja.io/), [Kanboard](https://kanboard.org/) -- open-source, generic, would need customization for job-specific fields.

**Recommendation**: Keep custom `tracker.yaml` + per-company markdown. JobSync is the only FOSS purpose-built tracker but too young; evaluate at v1.0+. Company markdown docs are unique value none of these replicate.

---

## D. AI Fit-Scoring / Job-Matching

### [Resume Matcher](https://github.com/srbhr/Resume-Matcher) -- strong standalone
- Apache-2.0 | ~26,600 stars | v1.2 (April 2, 2026) | Python, Docker, Ollama-capable
- Resume vs. JD scoring, tailoring, cover letter, multi-template PDF. Local-first.
- Gaps: Resume-centric, manual-paste workflow; not batch/pipeline-friendly.

### [AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk) -- avoid
- AGPL-3.0 | ~29,700 stars | **ARCHIVED April 16, 2026**. Auto-apply is ethically risky + violates LinkedIn ToS + antithetical to quality SRE search.

### [JobOps fit-scoring](https://github.com/DaKheera47/job-ops)
- 0-100 LLM scoring inline with scraping. Most operationally useful.

**Recommendation**: Keep custom `claude` CLI enrichment -- already tuned to our persona (IC-only, location tiers, SRE/Platform, GD rating). Consider Resume Matcher at *application* time (not discovery time) for tailored resume + cover letter PDFs.

---

## E. All-in-One Job-Search Copilots

- **[JobOps](https://github.com/DaKheera47/job-ops)** -- closest FOSS equivalent to our pipeline. Missing our sources + daily workflow. Worth a 2-week parallel eval.
- **AIHawk** -- archived, risky.
- **LazyApply** $49-99/mo SaaS -- auto-apply = bad for quality search.
- **Simplify Copilot** -- form-autofill utility, useful but not a pipeline.
- **Teal / Huntr / Careerflow / Jobright** -- SaaS, data lock-in, no daily workflow automation.

**Recommendation**: No all-in-one matches our local-first + custom criteria + daily workflow combination. JobOps is the only candidate worth evaluating seriously.

---

## F. Standardized Job-Posting Schemas

### [schema.org/JobPosting](https://schema.org/JobPosting) -- adopt this
- Dominant real-world standard; used by Google for Jobs. Most Greenhouse/Lever/Workday pages emit it as JSON-LD.
- Key fields: `title`, `hiringOrganization`, `jobLocation`, `baseSalary` (MonetaryAmount with currency/value/unitText), `employmentType`, `jobLocationType` (TELECOMMUTE), `datePosted`, `validThrough`, `applicantLocationRequirements`.

### HR Open Standards (HROS), JDX
- Over-engineered for personal use. Enterprise ATS interchange. Skip.

**Recommendation**: Use schema.org/JobPosting as the canonical internal schema for `normalize_job()`. Map our 13 CSV columns onto it:
- `title` -> Role
- `hiringOrganization.name` -> Company
- `jobLocation` -> Location
- `baseSalary` -> Comp (structured min/max/currency/interval)
- `datePosted` -> Date Found
- `url` -> Link
- `employmentType` -> IC filter input
- `jobLocationType` -> remote flag

Add pipeline-specific extensions: Fit, GD Rating, Status, Source, Product/Purpose, Notes.

---

## Verdict

### Replace with existing software

| Layer | Replace with | Why |
|---|---|---|
| LinkedIn/Indeed/Glassdoor/Google scraping | [JobSpy](https://github.com/speedyapply/JobSpy) | MIT, active, salary extraction built-in, handles rate limits. **Integration note**: JobSpy is not a drop-in config flag -- it must be registered as an entry in the `SCRAPERS` dict (`scrape-jobs.py:1836-1845`), added to (or deliberately excluded from) `NON_HEADLESS_SOURCES` (`:1850`), and given an enable flag under `job_search.scraper.sources`. The `type: playwright` field on `job_search.sources.boards[]` is consumed by `gather-jobs.sh` only; `scrape-jobs.py` ignores it. |
| RemoteOK scraping | [RemoteOK public API](https://remoteok.com/api) | No auth, clean salary fields |
| Resume/cover-letter tailoring (at apply time) | [Resume Matcher](https://github.com/srbhr/Resume-Matcher) | Local Ollama, strong PDF output |
| CSV schema | [schema.org/JobPosting](https://schema.org/JobPosting) field naming | De-facto standard, already in JSON-LD we parse |

### Keep custom

- HN Who's Hiring scraper (no tool covers this)
- Greenhouse board API scraper (already canonical pattern)
- Apple Careers scraper (proprietary, no aggregator)
- Wellfound scraper (no public API, blocks headless)
- WWR scraper (RSS feed is the upper bound)
- Tracker (tracker.yaml + company markdown docs) -- unique value
- Daily workflow slash commands -- no tool replaces this
- AI enrichment via `claude` CLI -- tuned to persona

### Proposed hybrid architecture

1. **Discovery**: JobSpy for LinkedIn/Indeed/Glassdoor/Google; RemoteOK API direct; Adzuna API supplemental for CA/US with salary_min/max; custom scrapers retained for HN, Greenhouse, Wellfound, WWR, Apple.
2. **Normalization**: `normalize_job()` maps to schema.org/JobPosting shape with extension fields for Fit/GD/Status/Source/Product/Notes.
3. **Dedup**: hash `(company_slug, role_slug, source)` before append. JobSpy's concurrent multi-board scraping makes cross-board dedup critical.
4. **Comp**: JobSpy `salary_source=direct_data` for its sources; RemoteOK native; JSON-LD baseSalary for the rest via existing detail-page enricher.
5. **AI enrichment**: Keep `claude` CLI for Fit/GD/Product/Notes. Add Resume Matcher at application time (not discovery time).
6. **Tracking**: Keep tracker.yaml + company markdown. Re-evaluate JobSync at v1.0.
7. **Daily workflow**: Keep all slash commands as-is.
8. **No paid fallbacks**: SerpApi, JSearch Pro, TheirStack, Apify rentals are all out of scope per user constraint. LinkedIn/Indeed coverage relies on JobSpy; if that breaks, the response is to fix/fork JobSpy, not subscribe.
9. **Skip JobOps parallel eval**: TypeScript stack, missing HN/Wellfound/Apple, Commons Clause. 2-week trial is predictably a no-go on the coverage gate. Not worth the time.
10. **Avoid**: AIHawk (archived + auto-apply risk), LazyApply (SaaS + auto-apply), any SaaS tracker (data sovereignty), JobFunnel (archived), all paid APIs.

---

## Z. Expanded Aggregator API Research

Deep dive on aggregators and ATS board APIs beyond the obvious candidates in §B.

### Z.1 Top pick: CareerJet API
- [Partner docs](https://www.careerjet.com/partners/api) -- API key, free
- **Best structured salary of any free aggregator found**: `salary`, `salary_currency_code`, `salary_min`, `salary_max`, `salary_type` (Y/M/W/D/H)
- Canadian support via `locale_code=en_CA`; separate `en_US` call with remote filter covers US remote
- Official Python SDK on PyPI
- **Verdict**: ADD. High value, low cost.

### Z.2 Top pick: Himalayas API
- [Docs](https://himalayas.app/docs/remote-jobs-api) -- **no auth**
- Structured salary: `minSalary`, `maxSalary` (annual, nullable), `currency` (ISO 4217)
- Remote-only, global, Canada-eligible roles when posted
- Refresh daily; "no benefit to polling more than once per day"; 429 on over-poll
- Max 20 results per page
- **Verdict**: ADD. Simplest drop-in with structured comp.

### Z.3 Top pick: Ashby public posting API (ATS)
- [Docs](https://developers.ashbyhq.com/docs/public-job-posting-api) -- no auth, per-company board name
- `GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}?includeCompensation=true`
- **Only ATS with public comp data**: `compensationTierSummary`, `compensationTiers[]` with Salary/Equity/Bonus components, `scrapeableCompensationSalarySummary`
- Growing fast among VC-backed tech startups (Linear, Loom, etc.) 2024-2026
- **Verdict**: ADD. Build curated Ashby target list (sources: fantastic.jobs/ats/ashby or manual).

### Z.4 Medium priority additions
- **Jooble** ([docs](https://jooble.org/api/about)) -- free API key via manual request. Salary is freetext passthrough (unstructured). Canadian coverage is a differentiator. Conditional USE.
- **Remotive** ([docs](https://remotive.com/remote-jobs/api)) -- no auth, remote-only, salary freetext, ~4 req/day limit. Low cost to add.
- **SmartRecruiters** ([docs](https://developers.smartrecruiters.com/docs/partners-job-board-api)) -- no auth, no salary. Adds ATS coverage.
- **Workable** ([docs](https://workable.readme.io/)) -- no auth, no salary. Adds ATS coverage.

### Z.5 Skip / dead / not relevant
| Name | Why skip |
|---|---|
| USAJobs | US federal only, no CA/private-sector |
| The Muse | No salary field |
| Arbeitnow | Europe-only |
| Reed.co.uk | UK-only |
| Hiring.cafe | No official API; Apify only |
| Hired.com | No API surface |
| Wellfound/AngelList | No public API |
| Canada Job Bank | No live API (monthly CSV only) |
| Workopolis / Monster Canada | Defunct 2025 |
| Eluta.ca | No API |
| StackOverflow Jobs | Dead (2022) |
| GitHub Jobs | Dead (2021) |
| Dice public API | Dead (recruiter-only remains) |
| ZipRecruiter Publisher | US-only per ToS |
| Indeed Publisher | Closed to new applicants since 2022 |
| Glassdoor Partner | Closed since 2021; reviews only anyway |
| Google Cloud Talent Solution | Employer-side, not consumer query API |
| Bing Jobs | Bing Search APIs retired August 2025 |
| 4dayweek.io / Dailyremote / Workew | No APIs |
| BambooHR / Teamtailor / Recruitee | Require per-company auth, limited NA coverage |

### Z.6 Top-10 aggregator ranking (for this use case)

| # | API | Auth | Salary Data | CA/Remote Coverage | Verdict |
|---|---|---|---|---|---|
| 1 | CareerJet | Free key | **Structured** (min/max/currency/type) | `en_CA` locale | USE |
| 2 | Himalayas | None | **Structured** (min/max annual + currency) | Remote global | USE |
| 3 | Jooble | Free key (manual) | Freetext | Global incl. Canada | USE (conditional) |
| 4 | Remotive | None | Freetext (sparse) | Remote global | USE |
| 5 | HN Firebase / Algolia | None | Freetext in comments | Global, strong SRE | Already have |
| 6 | WWR RSS | None | None | Remote global | Already have |
| 7 | Adzuna | Free key | **Structured** (min/max) | CA/US | Already in plan |
| 8 | RemoteOK | None | **Structured** (min/max) | Remote global | Already in plan |
| 9 | The Muse | Free key | None | US-heavy | Low priority |
| 10 | USAJobs | Free key | Federal pay grades | US federal only | Skip unless federal search |

### Z.7 ATS board APIs ranked for direct polling

| # | ATS | Auth | Salary | Status for our list |
|---|---|---|---|---|
| 1 | **Ashby** | None (board name) | **Structured with `?includeCompensation=true`** | ADD -- highest value |
| 2 | Greenhouse | None (board token) | No | Already in pipeline |
| 3 | Lever | None (org slug) | No (freetext only) | Already in pipeline |
| 4 | SmartRecruiters | None (company ID) | No | ADD (low priority) |
| 5 | Workable | None (client name) | No | ADD (low priority) |
| 6 | Recruitee | None (subdomain) | Unknown | Low priority, mostly EU |
| 7 | Teamtailor | Per-company key | Unknown | Skip (friction) |
| 8 | BambooHR | Key required | N/A | Skip (no public endpoint) |

### Z.8 Revised proposed architecture (supersedes §Verdict item 1)

**Constraint**: No paid software. All additions below are free-tier or open-source. Paid fallbacks previously considered (SerpApi, JSearch Pro, TheirStack, Apify rentals) are **out of scope**.

**Source-count triage**: the current pipeline has 8 sources and reliability issues (see investigation doc §2). Doubling to 16+ sources before fixing reliability amplifies silent-failure surface. **Phase additions rather than adopting all at once.**

**Phase 1 (fix reliability first)**: apply investigation doc Quick-fix + Medium-cost tiers. No new sources until enrichment-state tracking and `flock` are in place.

**Phase 2 (add only two)**:
  - **CareerJet** -- structured salary, CA locale support, highest signal-to-integration-cost ratio.
  - **Himalayas** -- structured salary, no auth, remote-global.

**Phase 3 (defer until Phase 2 proves stable over 2-4 weeks)**:
  - Adzuna (free key, CA/US structured salary).
  - Ashby ATS polling -- **requires a curated board-slug list** (no catalog endpoint exists). Start with 5-10 known relevant companies; do not attempt broad ATS sweep.
  - HN Who's Hiring: switch to Algolia endpoint (§Z.9).

**Phase 4 (skip unless clear need)**:
  - Jooble, Remotive, SmartRecruiters, Workable. Each adds an integration surface with low marginal yield (no structured comp, or low CA/remote overlap). Only add if Phases 2-3 leave a measurable coverage gap.

**Scraping layer** -- last resort, only where no API exists:
   - HN Who's Hiring (Algolia endpoint)
   - Wellfound (Playwright, visible browser)
   - Apple Careers (intercept API)
   - WWR (prefer RSS over HTML scrape)
   - LinkedIn + Indeed -- delegate to JobSpy once integrated

This ordering means every job goes through the cheapest reliable path first, and source count grows only when the previous phase is stable.

### Z.9 HN Who's Hiring: use Algolia, not Firebase

The current scraper [UNVERIFIED which endpoint it uses] should use `https://hn.algolia.com/api/v1/search?tags=comment,story_{THREAD_ID}&hitsPerPage=1000` to pull the full thread in one request, rather than recursive Firebase item-by-item calls. `hnhiring.com` already uses this approach.

---

## Sources

- [JobSpy GitHub](https://github.com/speedyapply/JobSpy)
- [JobFunnel GitHub](https://github.com/PaulMcInnis/JobFunnel) (archived)
- [py-linkedin-jobs-scraper GitHub](https://github.com/spinlud/py-linkedin-jobs-scraper)
- [AIHawk GitHub](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk) (archived)
- [JobOps GitHub](https://github.com/DaKheera47/job-ops)
- [JobSync GitHub](https://github.com/Gsync/jobsync)
- [Resume Matcher GitHub](https://github.com/srbhr/Resume-Matcher)
- [JSearch API](https://www.openwebninja.com/api/jsearch)
- [SerpApi Google Jobs](https://serpapi.com/google-jobs-api)
- [Adzuna Developer API](https://developer.adzuna.com/)
- [RemoteOK API](https://www.freepublicapis.com/remote-ok-jobs-api)
- [Jobicy RSS/API](https://jobicy.com/jobs-rss-feed)
- [TheirStack](https://theirstack.com/en/job-posting-api)
- [Apify](https://apify.com/pricing)
- [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html)
- [schema.org/JobPosting](https://schema.org/JobPosting)
- [HR Open Standards](https://www.hropenstandards.org/)
