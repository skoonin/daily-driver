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


class JobsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results_wanted_per_query: int = Field(default=50, description="")
    hours_old: int = Field(default=168, description="")
    country_indeed: str = Field(default="USA", description="")


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


class HackerNewsToggle(SourceToggle):
    """HN source toggle plus its per-source post cap.

    Shared shape for both `hn_who_is_hiring` and `hn_jobs` — each owns its own
    `hn_max_posts` cap.
    """

    hn_max_posts: int = Field(default=500, description="")


class JobspyToggle(SourceToggle):
    """Per-site enable flags for the JobSpy aggregator plus its query knobs."""

    linkedin: bool = Field(default=True, description="")
    indeed: bool = Field(default=True, description="")
    google: bool = Field(default=True, description="")
    jobs: JobsConfig = Field(default=JobsConfig(), description="")


# Maps a source key to the SourceToggle subclass that carries its per-source
# knobs. Keys absent here coerce to the bare SourceToggle (enable/disable only).
_SOURCE_TOGGLE_TYPES: dict[str, type[SourceToggle]] = {
    "weworkremotely": WeWorkRemotelyToggle,
    "greenhouse": GreenhouseToggle,
    "hn_who_is_hiring": HackerNewsToggle,
    "hn_jobs": HackerNewsToggle,
    "jobspy": JobspyToggle,
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
        default=4,
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


class EnrichmentConfig(BaseModel):
    """Knobs for the post-scrape enrichment passes (comp, fit, notes)."""

    model_config = ConfigDict(extra="forbid")

    enrich_timeout: int = Field(
        default=30,
        description="Seconds before a single enrichment LLM call is abandoned.",
    )
    max_enrich_companies: int = Field(
        default=50,
        description="Cap on company-description lookups per run (bounds API cost).",
    )
    enrich_gd_rating: bool = Field(
        default=True,
        description="Look up each company's Glassdoor rating during enrichment.",
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
    max_enrich_fit: int = Field(
        default=50,
        description="Cap on fit/notes LLM calls per run (bounds API cost).",
    )
    detail_delay_seconds: float = Field(
        default=0.5,
        description="Pause between detail-page fetches, in seconds, to avoid rate limits.",
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
    home_city: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "Vancouver, BC"},
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
            "Post-scrape enrichment: optional LLM passes that add Product,\n"
            "Glassdoor rating, a fit score, and a Notes summary. Each pass\n"
            "costs API calls; the max_* values cap how many run."
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
                "apple": False,
                "jobspy": {
                    "enabled": True,
                    "linkedin": True,
                    "indeed": True,
                    "google": True,
                    "jobs": {
                        "results_wanted_per_query": 50,
                        "hours_old": 168,
                        "country_indeed": "USA",
                    },
                },
            },
            "template_example_field_comments": {
                "weworkremotely": {"wwr_categories": _WWR_CATEGORIES_DESC},
                "greenhouse": {"greenhouse_boards": _GREENHOUSE_BOARDS_DESC},
            },
            "template_example_inline_comments": {
                "remoteok": "remoteok.com RSS",
                "weworkremotely": "weworkremotely.com listings",
                "hn_who_is_hiring": 'HN monthly "Who is hiring?" thread',
                "hn_jobs": "HN curated jobs (YC-funded company posts)",
                "greenhouse": "Greenhouse boards (configurable list)",
                "apple": "jobs.apple.com (API intercept)",
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
                "jobspy": (
                    "python-jobspy aggregator. Each site runs as a separate parallel\n"
                    "scraper (requires `python-jobspy>=1.1.82`, fetched with\n"
                    "linkedin_fetch_description=True). Glassdoor is intentionally\n"
                    "excluded — JobSpy's Glassdoor path returns HTTP 400 (\"location\n"
                    'not parsed") on every request as of 2026-04.'
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
