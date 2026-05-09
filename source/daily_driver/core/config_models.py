from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class DailyDriverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: str = "."


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    citizenship: list[str] = []
    work_auth: dict[str, str] = {}
    timezone: str | None = None
    seeking_since: date | None = None

    @field_validator("seeking_since", mode="before")
    @classmethod
    def _coerce_seeking_since(cls, v: Any) -> Any:
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v


class RecurringTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    cadence: Literal["daily", "weekly", "monthly"]
    estimated_minutes: int | None = None
    day: str | None = None

    @model_validator(mode="after")
    def _day_only_for_weekly(self) -> RecurringTask:
        if self.day is not None and self.cadence != "weekly":
            raise ValueError(
                f"'day' is only valid when cadence is 'weekly', got '{self.cadence}'"
            )
        return self


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formality: Literal["formal", "professional-casual", "casual"] | None = None
    sentence_length: Literal["short", "medium", "long"] | None = None
    avoid_words: list[str] = []
    preferred_signoff: str | None = None


class TrackerCategoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: list[str] = []


class TrackerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_category: str = "task"
    categories: dict[str, TrackerCategoryConfig] = {}

    @model_validator(mode="after")
    def _default_category_in_categories(self) -> TrackerConfig:
        if self.default_category not in self.categories:
            raise ValueError(
                f"default_category '{self.default_category}' must be a key in categories"
            )
        return self


class Compensation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: Literal["USD", "CAD", "EUR", "GBP"] = "USD"
    minimum: int
    target: int
    current: int | None = None
    maximum: int | None = None


class Locations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home_city: str | None = None
    remote: bool = False
    countries: list[str] = []
    cities: list[str] = []


class RoleFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    levels: list[str] = []
    exclude_management: bool = False
    exclude_keywords: list[str] = []


class JobSpyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results_wanted_per_query: int = 50
    hours_old: int = 168
    country_indeed: str = "USA"


class PlaywrightDelays(BaseModel):
    """Per-source Playwright timing knobs (milliseconds)."""

    model_config = ConfigDict(extra="forbid")

    page_load_ms: int = 3000
    interaction_ms: int = 500
    settle_ms: int = 250


class SourceToggle(BaseModel):
    """Per-source enable/disable plus optional source-specific options."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class ScraperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    timeout: int = 30
    enrich_timeout: int = 30
    max_enrich_companies: int = 50
    enrich_gd_rating: bool = True
    enrich_fit: bool = True
    enrich_notes: bool = True
    max_enrich_fit: int = 50
    detail_delay_seconds: float = 0.5
    search_terms: list[str] | None = None
    headless: bool = False
    wwr_categories: list[str] = []
    hn_max_posts: int = 100
    greenhouse_boards: list[str] = ["anthropic"]
    jobspy: JobSpyConfig = JobSpyConfig()
    playwright_delays: dict[str, PlaywrightDelays] = {}
    sources: dict[str, SourceToggle] = {}
    parallel_workers: int = 4
    max_pages: int = 3

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, v: Any) -> Any:
        # Migrate legacy `sources: {linkedin: true}` form to the typed
        # SourceToggle model. Accepts bool, dict, or already-typed instances.
        if not isinstance(v, dict):
            return v
        out: dict[str, Any] = {}
        for key, val in v.items():
            if isinstance(val, bool):
                out[key] = SourceToggle(enabled=val)
            else:
                out[key] = val
        return out


class JobSearchPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: str | None = None
    locations: Locations | None = None
    compensation: Compensation | None = None
    role_filters: RoleFilters | None = None
    roles: list[str] = []
    domain_keywords: list[str] = []
    seniority_keywords: list[str] = []
    min_comp_usd: int = 180000
    scraper: ScraperConfig = ScraperConfig()
    sources: dict[str, Any] = {}


class PluginsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_search: JobSearchPlugin | None = None


class GatherGitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_paths: list[Path] = []


class GatherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    git: GatherGitConfig = GatherGitConfig()


class Config(BaseModel):
    # Root model is extra="allow" so users can stash workspace-local keys
    # (e.g., notes for their own slash commands) without a schema bump.
    # Nested plugin schemas remain extra="forbid" — typos there are real bugs.
    model_config = ConfigDict(extra="allow")

    daily_driver: DailyDriverConfig = DailyDriverConfig()
    user_profile: UserProfile = UserProfile()
    recurring_tasks: list[RecurringTask] = []
    scheduler: dict[str, Any] | None = None
    voice_profile: VoiceProfile = VoiceProfile()
    tracker: TrackerConfig
    gather: GatherConfig = GatherConfig()
    plugins: PluginsConfig = PluginsConfig()
