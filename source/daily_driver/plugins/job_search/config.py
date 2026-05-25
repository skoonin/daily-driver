"""Config models for the job_search plugin namespace (plugins.job_search)."""

from __future__ import annotations

from typing import Any, Literal

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


class JobspyToggle(SourceToggle):
    """Per-site enable flags for the JobSpy aggregator."""

    linkedin: bool = Field(default=True, description="")
    indeed: bool = Field(default=True, description="")
    google: bool = Field(default=True, description="")


class ScraperConfig(BaseModel):
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
    sources: dict[str, SourceToggle] = Field(
        default={},
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
    search_terms: list[str] | None = Field(
        default=None, description="", json_schema_extra={"template_skip": True}
    )
    headless: bool = Field(
        default=False, description="", json_schema_extra={"template_skip": True}
    )
    wwr_categories: list[str] = Field(
        default=[], description="", json_schema_extra={"template_skip": True}
    )
    hn_max_posts: int = Field(
        default=100, description="", json_schema_extra={"template_skip": True}
    )
    greenhouse_boards: list[str] = Field(
        default=["anthropic"], description="", json_schema_extra={"template_skip": True}
    )
    jobs: JobsConfig = Field(
        default=JobsConfig(), description="", json_schema_extra={"template_skip": True}
    )
    parallel_workers: int = Field(
        default=4, description="", json_schema_extra={"template_skip": True}
    )
    max_pages: int = Field(
        default=3, description="", json_schema_extra={"template_skip": True}
    )

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        out: dict[str, Any] = {}
        for key, val in v.items():
            if key == "jobspy":
                out[key] = JobspyToggle.model_validate(
                    val if isinstance(val, dict) else {"enabled": bool(val)}
                )
            elif isinstance(val, bool):
                out[key] = SourceToggle(enabled=val)
            else:
                out[key] = val
        return out


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
    min_comp_usd: int = Field(default=180000, description="")
    locations: Locations | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example_model": True},
    )
    primary_currency: Literal["USD", "CAD", "EUR", "GBP"] | None = Field(
        default=None,
        description=(
            "primary_currency lives on `job_search`, NOT on `scraper`. When set,\n"
            "drops scraped jobs whose parsed comp currency doesn't match.\n"
            "Unparseable comp passes through. Unset = no filter. Decoupled from\n"
            "`min_comp_usd` (that drives the user's pay-floor input; this prunes\n"
            "the input stream by source-currency)."
        ),
        json_schema_extra={
            "template_example": "USD",
            "inline_comment": "USD | CAD | EUR | GBP",
        },
    )
    scraper: ScraperConfig = Field(
        default=ScraperConfig(),
        description="",
    )
    sources: dict[str, Any] = Field(
        default={},
        description="",
        json_schema_extra={"template_skip": True},
    )
