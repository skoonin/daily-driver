"""Config models for the job_search plugin namespace (plugins.job_search)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    cities: list[str] = Field(default=[], description="")


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

    wwr_categories: list[str] = Field(
        default=[], description="", json_schema_extra={"template_skip": True}
    )


class GreenhouseToggle(SourceToggle):
    """Greenhouse source toggle plus its per-source board slug list."""

    greenhouse_boards: list[str] = Field(
        default=["anthropic"], description="", json_schema_extra={"template_skip": True}
    )


class HackerNewsToggle(SourceToggle):
    """HN source toggle plus its per-source post cap.

    Shared shape for both `hn_who_is_hiring` and `hn_jobs` — each owns its own
    `hn_max_posts` cap.
    """

    hn_max_posts: int = Field(
        default=100, description="", json_schema_extra={"template_skip": True}
    )


class JobspyToggle(SourceToggle):
    """Per-site enable flags for the JobSpy aggregator plus its query knobs."""

    linkedin: bool = Field(default=True, description="")
    indeed: bool = Field(default=True, description="")
    google: bool = Field(default=True, description="")
    jobs: JobsConfig = Field(
        default=JobsConfig(), description="", json_schema_extra={"template_skip": True}
    )


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
        description="",
        json_schema_extra={"template_skip": True},
    )
    timeout: int = Field(
        default=30, description="", json_schema_extra={"template_skip": True}
    )
    search_terms: list[str] | None = Field(
        default=None, description="", json_schema_extra={"template_skip": True}
    )
    headless: bool = Field(
        default=False, description="", json_schema_extra={"template_skip": True}
    )
    parallel_workers: int = Field(
        default=4, description="", json_schema_extra={"template_skip": True}
    )
    max_pages: int = Field(
        default=3, description="", json_schema_extra={"template_skip": True}
    )


class EnrichmentConfig(BaseModel):
    """Knobs for the post-scrape enrichment passes (comp, fit, notes)."""

    model_config = ConfigDict(extra="forbid")

    enrich_timeout: int = Field(
        default=30, description="", json_schema_extra={"template_skip": True}
    )
    max_enrich_companies: int = Field(
        default=50, description="", json_schema_extra={"template_skip": True}
    )
    enrich_gd_rating: bool = Field(
        default=True, description="", json_schema_extra={"template_skip": True}
    )
    enrich_fit: bool = Field(
        default=True, description="", json_schema_extra={"template_skip": True}
    )
    enrich_notes: bool = Field(
        default=True, description="", json_schema_extra={"template_skip": True}
    )
    max_enrich_fit: int = Field(
        default=50, description="", json_schema_extra={"template_skip": True}
    )
    detail_delay_seconds: float = Field(
        default=0.5, description="", json_schema_extra={"template_skip": True}
    )


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
    min_comp_usd: int = Field(
        default=180000,
        description=(
            "Pay floor, compared directly against each listing's own comp figure\n"
            "with no currency conversion. Jobs whose found comp is below it are\n"
            "kept but marked `skipped-comp`; jobs with no listed comp are surfaced."
        ),
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
        description="",
        json_schema_extra={"template_skip": True},
    )
    sources: dict[str, SourceToggle] = Field(
        default_factory=dict,
        description="",
        json_schema_extra={
            "template_example": {
                "remoteok": False,
                "weworkremotely": False,
                "hn_who_is_hiring": False,
                "hn_jobs": False,
                "greenhouse": False,
                "apple": False,
                "jobspy": {
                    "enabled": True,
                    "linkedin": True,
                    "indeed": True,
                    "google": True,
                },
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
