"""Config models for the job_search plugin namespace (plugins.job_search)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Shared so the toggle field and the sources-block template comment stay in sync.
_WWR_CATEGORIES_DESC = (
    'WeWorkRemotely category slugs to fetch, e.g. "devops-sysadmin"\n'
    "(becomes /categories/remote-devops-sysadmin-jobs.rss). An empty list\n"
    "scrapes nothing; this source needs at least one category."
)
_GREENHOUSE_BOARDS_DESC = (
    'Greenhouse board slugs to scrape (the "<slug>" in\n'
    "boards.greenhouse.io/<slug>). Each board's full posting list is fetched\n"
    "in one request; add the companies you are targeting."
)
_ASHBY_BOARDS_DESC = (
    'AshbyHQ board slugs to scrape (the "<slug>" in\n'
    "jobs.ashbyhq.com/<slug>). Each board's full posting list is fetched in\n"
    "one request; add the companies you are targeting. Slugs are\n"
    "case-sensitive (e.g. Notion)."
)
_WORKABLE_ACCOUNTS_DESC = (
    'Workable account slugs to scrape (the "<slug>" in\n'
    "apply.workable.com/<slug>). Each account's full posting list is fetched\n"
    "in one request; add the companies you are targeting (e.g. huggingface)."
)
_WORKDAY_BOARDS_DESC = (
    "Workday careers sites to scrape, each named by the three parts of its\n"
    "URL. For crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers that is\n"
    "{tenant: crowdstrike, host: wd5, site: crowdstrikecareers}. Each board is\n"
    "paginated through Workday's public search API; set the optional `company`\n"
    "to override the display name (defaults to the title-cased tenant)."
)


class Locations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home_city: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "Vancouver, BC"},
    )
    remote: bool = Field(
        default=False,
        description="",
        json_schema_extra={"template_example": True},
    )
    countries: list[str] = Field(
        default=[],
        description="",
        json_schema_extra={"template_example": ["CA", "US"]},
    )


class SourceToggle(BaseModel):
    """Per-source enable/disable plus optional source-specific options."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="")


class WeWorkRemotelyToggle(SourceToggle):
    """WeWorkRemotely source toggle plus its per-source category list."""

    wwr_categories: list[str] = Field(default=[], description=_WWR_CATEGORIES_DESC)


class GreenhouseToggle(SourceToggle):
    """Greenhouse source toggle plus its per-source board slug list."""

    greenhouse_boards: list[str] = Field(
        default=["anthropic"], description=_GREENHOUSE_BOARDS_DESC
    )


class AshbyToggle(SourceToggle):
    """AshbyHQ source toggle plus its per-source board slug list."""

    ashby_boards: list[str] = Field(default=[], description=_ASHBY_BOARDS_DESC)


class WorkableToggle(SourceToggle):
    """Workable source toggle plus its per-source account slug list."""

    workable_accounts: list[str] = Field(
        default=[], description=_WORKABLE_ACCOUNTS_DESC
    )


class WorkdayBoard(BaseModel):
    """One Workday careers site, named by the three parts of its URL.

    For ``crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers`` that is
    ``tenant=crowdstrike``, ``host=wd5``, ``site=crowdstrikecareers``. The
    Workday payload has no company field (the tenant is the company), so
    ``company`` is an optional display-name override; it defaults to the
    title-cased tenant.
    """

    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(description="The <tenant> in <tenant>.<host>.myworkdayjobs.com")
    host: str = Field(description="The <host> data-center segment, e.g. wd5")
    site: str = Field(
        description="The careers-site path segment, e.g. crowdstrikecareers"
    )
    company: str | None = Field(
        default=None,
        description="Display name for the company; defaults to the title-cased tenant.",
    )


class WorkdayToggle(SourceToggle):
    """Workday source toggle plus its per-source list of careers sites."""

    workday_boards: list[WorkdayBoard] = Field(
        default=[], description=_WORKDAY_BOARDS_DESC
    )


class HackerNewsToggle(SourceToggle):
    """HN source toggle plus its per-source post cap.

    Shared shape for both `hn_who_is_hiring` and `hn_jobs` — each owns its own
    `hn_max_posts` cap.
    """

    hn_max_posts: int = Field(default=500, description="")


class LinkedInToggle(SourceToggle):
    """LinkedIn source toggle plus its query knobs.

    LinkedIn and Indeed are fetched via python-jobspy (an implementation
    detail); the user surface is the site name. ``scrape_jobs`` has no LinkedIn
    country parameter, so — unlike :class:`IndeedToggle` — there is no
    ``country`` knob here.
    """

    results_wanted_per_query: int = Field(default=50, description="")
    hours_old: int = Field(default=168, description="")


class IndeedToggle(SourceToggle):
    """Indeed source toggle plus its query knobs.

    ``country`` maps to python-jobspy's ``country_indeed`` parameter and lives
    only here because that is the one ``scrape_jobs`` site it affects. It is the
    fallback used when a configured ISO country code is not in JobSpy's enum.
    """

    results_wanted_per_query: int = Field(default=50, description="")
    hours_old: int = Field(default=168, description="")
    country: str = Field(default="USA", description="")


# Maps a source key to the SourceToggle subclass that carries its per-source
# knobs. Keys absent here coerce to the bare SourceToggle (enable/disable only).
_SOURCE_TOGGLE_TYPES: dict[str, type[SourceToggle]] = {
    "weworkremotely": WeWorkRemotelyToggle,
    "greenhouse": GreenhouseToggle,
    "ashby": AshbyToggle,
    "workable": WorkableToggle,
    "workday": WorkdayToggle,
    "hn_who_is_hiring": HackerNewsToggle,
    "hn_jobs": HackerNewsToggle,
    "linkedin": LinkedInToggle,
    "indeed": IndeedToggle,
}


class ScraperConfig(BaseModel):
    """Transport / retry knobs shared by every scraper source."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="")
    max_retries: int = Field(
        default=3,
        description=(
            "Retries for HTTP 429/503 responses (rate limits). Honors the\n"
            "Retry-After header when present; otherwise applies exponential\n"
            "backoff (1.5s, 3s, 6s, ... capped at 30s). Set to 0 to disable."
        ),
    )
    max_age_days: int = Field(
        default=30,
        description=(
            "Maximum age (in days) for scraped postings. Currently applied to\n"
            "`hn_jobs` (which has 17k+ historical entries via Algolia). Set to\n"
            "0 to disable the filter. Other sources are implicitly recent."
        ),
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
            "Gecko/20100101 Firefox/128.0"
        ),
        description="User-Agent header sent by the HTTP/RSS scrapers.",
    )
    timeout: int = Field(
        default=30,
        description="Per-source HTTP / browser timeout, in seconds.",
    )
    search_terms: list[str] | None = Field(
        default=None,
        description=(
            "Override the search terms sent to query-based sources (JobSpy,\n"
            "Apple). When unset, terms are derived from roles and keywords."
        ),
    )
    # Overridden per scrape phase by the orchestrator (_ctx_with_headless), so a
    # user value has no effect; deliberately kept out of the generated template.
    headless: bool = Field(
        default=False, description="", json_schema_extra={"template_skip": True}
    )
    parallel_workers: int = Field(
        default=8,
        description="Worker threads for the parallel (headless) scrape phase.",
    )
    max_pages: int = Field(
        default=3,
        description="Pagination / scroll passes per search term (Apple, JobSpy).",
    )
    browser: Literal["firefox", "chromium", "webkit"] = Field(
        default="firefox",
        description=(
            "Playwright engine for browser-driven sources (Apple). One of\n"
            "firefox, chromium, webkit. The chosen engine must be installed\n"
            "(`playwright install <engine>`)."
        ),
    )


class Criterion(BaseModel):
    """One user-declared thing to look for in a job description.

    `assess` is a natural-language instruction the enrichment LLM reasons
    about (so polarity and negation survive), not a keyword to match.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="")
    assess: str = Field(description="")

    @field_validator("label", "assess")
    @classmethod
    def _single_line_non_empty(cls, value: str) -> str:
        # label rides the prompt and the "Label: value" Notes segment; newlines
        # would break the single-line Notes/CSV invariant.
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        if "\n" in cleaned or "\r" in cleaned:
            raise ValueError("must be a single line")
        return cleaned


class PhaseRoute(BaseModel):
    """Per-phase AI provider/model override for one enrichment pass.

    A local 2-field mirror of core's `AITaskConfig`, defined here rather than
    imported: `core/config_models.py` eagerly imports the plugin config chain
    before `AITaskConfig` is bound, so importing it from core would raise an
    ImportError at startup. Plugins never import core (see module docstring of
    `plugins/config.py`). Both fields default to None (= "inherit from the
    resolution chain"); the resolver supplies the terminal claude fallback.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] | None = Field(
        default=None,
        description=(
            "provider: claude | ollama. Unset inherits the enrichment domain\n"
            "default, then the global `ai.provider`, then claude."
        ),
        json_schema_extra={"template_example": "claude"},
    )
    model: str | None = Field(
        default=None,
        description=(
            'Provider-specific model identifier. For claude: "sonnet", "haiku",\n'
            'etc. For ollama: a pulled tag like "qwen2.5:7b" or "phi4". A model\n'
            "applies regardless of which provider this phase resolves to (provider\n"
            "and model are chosen independently), so do not leave a claude model\n"
            "set on a phase you route to ollama, or vice versa."
        ),
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )


class EnrichmentConfig(BaseModel):
    """Knobs for the post-scrape enrichment pass (comp, fit, notes).

    Owns its own AI provider routing: enrichment is plugin-specific, so the
    provider/model selection lives here rather than leaking into core's `ai:`
    block. The provider-connection blocks (parallelism, ollama endpoint /
    timeout) are still shared core infra under `ai.claude:` / `ai.ollama:`.
    The single LLM pass — `fit_notes` (fit / notes / remote / criteria) — takes
    an optional per-phase route block that overrides these domain defaults.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] | None = Field(
        default=None,
        description=(
            "Domain default provider for the enrichment pass. Unset inherits\n"
            "the global `ai.provider`, then claude. The per-phase block overrides it."
        ),
        json_schema_extra={"template_example": "claude"},
    )
    model: str | None = Field(
        default=None,
        description=(
            'Provider-specific model identifier. For claude: "sonnet", "haiku",\n'
            'etc. For ollama: a pulled tag like "qwen2.5:14b" or "phi4". A model\n'
            "applies regardless of which provider a phase resolves to (provider\n"
            "and model are chosen independently), so a domain model paired with a\n"
            "per-phase provider override must be compatible with that provider."
        ),
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )
    fit_notes: PhaseRoute = Field(
        default=PhaseRoute(),
        description=(
            "Route override for the fit/notes pass (fit score, notes, remote,\n"
            "criteria, one call per job)."
        ),
    )
    enrich_timeout: int = Field(
        default=30,
        description="Seconds before a single enrichment LLM call is abandoned.",
    )
    enrich_fit: bool = Field(
        default=True,
        description="Score each job 1-10 for fit against your persona and locations.",
    )
    enrich_notes: bool = Field(
        default=True,
        description=(
            "Generate a one-line Notes summary (tech stack, remote policy, red flags)."
        ),
    )
    enrich_is_remote: bool = Field(
        default=True,
        description=(
            "Judge each job remote/hybrid/onsite during the fit/notes pass "
            "(no extra LLM call)."
        ),
    )
    max_enrich_fit: int = Field(
        default=50,
        description="Cap on fit/notes LLM calls per run (bounds API cost).",
    )
    detail_delay_seconds: float = Field(
        default=0.5,
        description="Pause between detail-page fetches, in seconds, to avoid rate limits.",
    )
    fetch_linkedin_descriptions: bool = Field(
        default=False,
        description=(
            "Fetch login-free LinkedIn job pages during enrichment to fill\n"
            "missing job descriptions, so the fit/notes pass can write Notes for\n"
            "LinkedIn rows. Bounded by max_enrich_fit, fill-missing-only, and\n"
            "politely throttled. Anonymous fetches are often redirected to a\n"
            "signup wall, so coverage is good-but-partial by nature."
        ),
    )
    criteria: list[Criterion] = Field(
        default=[],
        description=(
            "Extra things to assess in each job description. Each becomes a\n"
            '"Label: value" segment appended to Notes. `assess` is a\n'
            "natural-language instruction the LLM reasons about, not a\n"
            "keyword match."
        ),
        json_schema_extra={
            "template_example": [
                {
                    "label": "Sponsorship",
                    "assess": "Does the role offer visa sponsorship?",
                }
            ]
        },
    )

    @field_validator("criteria")
    @classmethod
    def _unique_labels(cls, value: list[Criterion]) -> list[Criterion]:
        # The LLM keys its response object by label; duplicates collapse to one
        # value yet would fold multiple times into Notes.
        labels = [c.label for c in value]
        if len(labels) != len(set(labels)):
            raise ValueError("criteria labels must be unique")
        return value


class JobSearchPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "Senior SRE / Platform Engineer"},
    )
    roles: list[str] = Field(
        default=[],
        description="",
        json_schema_extra={"inline_comment": '["SRE", "Platform Engineer"]'},
    )
    domain_keywords: list[str] = Field(
        default=[],
        description="",
        json_schema_extra={"inline_comment": '["kubernetes", "observability"]'},
    )
    seniority_keywords: list[str] = Field(
        default=[],
        description="",
        json_schema_extra={"inline_comment": '["senior", "staff", "principal"]'},
    )
    locations: Locations | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example_model": True},
    )
    scraper: ScraperConfig = Field(
        default=ScraperConfig(),
        description="",
    )
    enrichment: EnrichmentConfig = Field(
        default=EnrichmentConfig(),
        description=(
            "Post-scrape enrichment: an optional LLM pass that adds a fit score\n"
            "and a Notes summary. The pass costs API calls; max_enrich_fit caps\n"
            "how many run."
        ),
    )
    sources: dict[str, SourceToggle] = Field(
        default_factory=dict,
        description="",
        json_schema_extra={
            "template_example": {
                "remoteok": False,
                "weworkremotely": {"enabled": False, "wwr_categories": []},
                "hn_who_is_hiring": {"enabled": False, "hn_max_posts": 500},
                "hn_jobs": {"enabled": False, "hn_max_posts": 500},
                "greenhouse": {"enabled": False, "greenhouse_boards": ["anthropic"]},
                "ashby": {"enabled": False, "ashby_boards": []},
                "workable": {"enabled": False, "workable_accounts": []},
                "workday": {
                    "enabled": False,
                    "workday_boards": [
                        {
                            "tenant": "crowdstrike",
                            "host": "wd5",
                            "site": "crowdstrikecareers",
                            "company": "CrowdStrike",
                        }
                    ],
                },
                "apple": False,
                "linkedin": {
                    "enabled": True,
                    "results_wanted_per_query": 50,
                    "hours_old": 168,
                },
                "indeed": {
                    "enabled": True,
                    "results_wanted_per_query": 50,
                    "hours_old": 168,
                    "country": "USA",
                },
            },
            "template_example_field_comments": {
                "weworkremotely": {"wwr_categories": _WWR_CATEGORIES_DESC},
                "greenhouse": {"greenhouse_boards": _GREENHOUSE_BOARDS_DESC},
                "ashby": {"ashby_boards": _ASHBY_BOARDS_DESC},
                "workable": {"workable_accounts": _WORKABLE_ACCOUNTS_DESC},
                "workday": {"workday_boards": _WORKDAY_BOARDS_DESC},
            },
            "template_example_inline_comments": {
                "remoteok": "remoteok.com RSS",
                "weworkremotely": "weworkremotely.com listings",
                "hn_who_is_hiring": 'HN monthly "Who is hiring?" thread',
                "hn_jobs": "HN curated jobs (YC-funded company posts)",
                "greenhouse": "Greenhouse boards (configurable list)",
                "ashby": "AshbyHQ boards (configurable list)",
                "workable": "Workable accounts (configurable list)",
                "workday": "Workday careers sites (configurable list)",
                "apple": "jobs.apple.com (API intercept)",
                "indeed": "indeed.com (country sets the regional host)",
            },
            "template_example_block_comments": {
                "remoteok": (
                    "All keys are optional; omitted = on (SourceToggle defaults\n"
                    "enabled=true). List a key with `false` to disable it.\n"
                    "\n"
                    "Scrapers shipped in this repo (HTTP/RSS, no extra deps):"
                ),
                "apple": (
                    "Scrapers shipped in this repo (Playwright, needs"
                    " `playwright install`):"
                ),
                "linkedin": (
                    "LinkedIn + Indeed (each its own source). Each enabled site\n"
                    "is fetched separately, under its own progress row. country\n"
                    "(Indeed only) sets the regional host and is the fallback for\n"
                    "countries the scraper does not recognize. Requires\n"
                    "`python-jobspy>=1.1.82`."
                ),
            },
        },
    )

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        out: dict[str, Any] = {}
        for key, val in v.items():
            toggle_cls = _SOURCE_TOGGLE_TYPES.get(key, SourceToggle)
            if isinstance(val, bool):
                out[key] = toggle_cls(enabled=val)
            elif isinstance(val, dict):
                out[key] = toggle_cls.model_validate(val)
            else:
                out[key] = val
        return out
