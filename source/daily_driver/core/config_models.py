from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DailyDriverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: str = Field(
        default=".",
        description=(
            "Where command output (jobs.csv, daily/, etc.) lives. Relative paths\n"
            "resolve against the workspace root."
        ),
    )


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": ""},
    )
    citizenship: list[str] = Field(
        default=[],
        description="",
        json_schema_extra={"inline_comment": '["US", "CA"]'},
    )
    work_auth: dict[str, str] = Field(
        default={},
        description="",
        json_schema_extra={"inline_comment": '{"US": "citizen", "CA": "PR"}'},
    )
    timezone: str | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_example": "",
            "inline_comment": '"America/Vancouver"',
        },
    )
    seeking_since: date | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_example": date(2026, 4, 6),  # type: ignore[dict-item]
            "inline_comment": "YYYY-MM-DD when active job search began",
        },
    )

    @field_validator("seeking_since", mode="before")
    @classmethod
    def _coerce_seeking_since(cls, v: Any) -> Any:
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v


class RecurringTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="")
    cadence: Literal["daily", "weekly", "monthly"] = Field(
        description="",
        json_schema_extra={"inline_comment": "daily | weekly | monthly"},
    )
    estimated_minutes: int | None = Field(default=None, description="")
    day: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"inline_comment": "only valid when cadence is weekly"},
    )

    @model_validator(mode="after")
    def _day_only_for_weekly(self) -> RecurringTask:
        if self.day is not None and self.cadence != "weekly":
            raise ValueError(
                f"'day' is only valid when cadence is 'weekly', got '{self.cadence}'"
            )
        return self


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formality: Literal["formal", "professional-casual", "casual"] | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_example": "professional-casual",
            "inline_comment": "formal | professional-casual | casual",
        },
    )
    sentence_length: Literal["short", "medium", "long"] | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_example": "medium",
            "inline_comment": "short | medium | long",
        },
    )
    avoid_words: list[str] = Field(default=[], description="")
    preferred_signoff: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "—"},
    )


class TrackerCategoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: list[str] = Field(default=[], description="")


class TrackerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_category: str = Field(default="task", description="")
    warn_unknown_status: bool = Field(
        default=True,
        description=(
            "Print a stderr nudge when `tracker add/update --status` is given a\n"
            "value outside the recommended set (open, in-progress, blocked, done,\n"
            "ruled-out). Entries still save with the custom status; the warning\n"
            "is a one-time hint. Set to false to silence."
        ),
        json_schema_extra={"template_commented": True},
    )
    categories: dict[str, TrackerCategoryConfig] = Field(
        default={"task": TrackerCategoryConfig(required=["title"])},
        description="",
        json_schema_extra={
            "trailing_comment": (
                "Add more categories as you need them. `required` lists fields that\n"
                "must be set on `tracker add`.\n"
                "errand:\n"
                "  required: [title]\n"
                "ticket:\n"
                "  required: [title, link]"
            ),
            "trailing_comment_indent": 2,
        },
    )

    @model_validator(mode="after")
    def _default_category_in_categories(self) -> TrackerConfig:
        if self.default_category not in self.categories:
            raise ValueError(
                f"default_category '{self.default_category}' must be a key in categories"
            )
        return self


class Compensation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: Literal["USD", "CAD", "EUR", "GBP"] = Field(
        default="USD",
        description="",
        json_schema_extra={"inline_comment": "USD | CAD | EUR | GBP"},
    )
    minimum: int = Field(description="", json_schema_extra={"template_example": 180000})
    target: int = Field(description="", json_schema_extra={"template_example": 240000})
    current: int | None = Field(
        default=None, description="", json_schema_extra={"template_skip": True}
    )
    maximum: int | None = Field(
        default=None, description="", json_schema_extra={"template_skip": True}
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
    cities: list[str] = Field(default=[], description="")


class RoleFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    levels: list[str] = Field(
        default=[], description="", json_schema_extra={"template_skip": True}
    )
    exclude_management: bool = Field(
        default=False,
        description="",
        json_schema_extra={"template_example": True},
    )
    exclude_keywords: list[str] = Field(default=[], description="")


class JobsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results_wanted_per_query: int = Field(default=50, description="")
    hours_old: int = Field(default=168, description="")
    country_indeed: str = Field(default="USA", description="")


class PlaywrightDelays(BaseModel):
    """Per-source Playwright timing knobs (milliseconds)."""

    model_config = ConfigDict(extra="forbid")

    page_load_ms: int = Field(default=3000, description="")
    interaction_ms: int = Field(default=500, description="")
    settle_ms: int = Field(default=250, description="")


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
    playwright_delays: dict[str, PlaywrightDelays] = Field(
        default={}, description="", json_schema_extra={"template_skip": True}
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
    compensation: Compensation | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example_model": True},
    )
    role_filters: RoleFilters | None = Field(
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
            "`compensation.currency` (that drives the user's pay-floor input;\n"
            "this prunes the input stream by source-currency)."
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


class PluginsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_search: JobSearchPlugin | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example_model": True},
    )


_HHMM_RE = re.compile(r"^([0-1]?\d|2[0-3]):([0-5]\d)$")


class ScheduleConfig(BaseModel):
    """Single source of truth for daily-driver's scheduled times.

    Drives BOTH the launchd plist install (`scheduler install` builds plists
    from these) AND `is_late_day` evaluation (so prompts can frame the agenda
    when the user starts late). HH:MM strings; 24-hour clock.
    """

    model_config = ConfigDict(extra="forbid")

    day_start: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "08:00", "template_quote": True},
    )
    day_end: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "17:30", "template_quote": True},
    )

    @field_validator("day_start", "day_end", mode="before")
    @classmethod
    def _validate_hhmm(cls, v: Any) -> Any:
        if v is None:
            return v
        if isinstance(v, int):
            hh, mm = divmod(v, 60)
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                v = f"{hh:02d}:{mm:02d}"
        if not isinstance(v, str) or not _HHMM_RE.match(v):
            raise ValueError(f"expected HH:MM time string, got {v!r}")
        return v


class ClaudeConfig(BaseModel):
    """Knobs for how daily-driver invokes the `claude` CLI."""

    model_config = ConfigDict(extra="forbid")

    resume_check_in: bool = Field(
        default=False,
        description=(
            "When true, `check-in` resumes the prior `claude` session instead of\n"
            "opening a fresh one. Useful if you keep one long-running session per\n"
            "day and want check-in to land in it."
        ),
    )


class AITaskConfig(BaseModel):
    """Provider + model selection for a single headless AI task."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] = Field(
        default="claude",
        description="provider: claude | ollama",
    )
    model: str | None = Field(
        default=None,
        description=(
            'Provider-specific model identifier. For claude: "sonnet", "haiku",\n'
            'etc. For ollama: a pulled tag like "qwen2.5:14b" or "phi4".'
        ),
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )


class OllamaConfig(BaseModel):
    """Settings consulted when a task is routed to the ollama provider."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(default="http://localhost:11434", description="")
    timeout: int = Field(
        default=60,
        description="Ollama is materially slower than the claude API; give it room.",
    )
    max_parallel: int = Field(
        default=4,
        ge=1,
        description="",
        json_schema_extra={"template_skip": True},
    )


class AIConfig(BaseModel):
    """Per-task provider routing for headless AI calls.

    Default resolves to `provider: claude` for every task, so omitting the
    `ai:` block in `.dd-config.yaml` preserves the existing claude-only
    behavior. Interactive launchers (day-start, check-in, day-end) ignore
    this block — they always use the `claude` CLI directly for session /
    agent / workspace-context features Ollama doesn't have.
    """

    model_config = ConfigDict(extra="forbid")

    enrichment: AITaskConfig = Field(default=AITaskConfig(), description="")
    summary: AITaskConfig = Field(default=AITaskConfig(), description="")
    ollama: OllamaConfig = Field(
        default=OllamaConfig(),
        description="Only consulted when a task is routed to the ollama provider.",
    )


class FocusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_duration: str = Field(
        default="25m",
        description=(
            "Fallback duration when `focus on` runs without `--for`.\n"
            'Accepts the same suffixes as `--for` (e.g. "25m", "1h", "90m").'
        ),
        json_schema_extra={"template_quote": True},
    )


class GatherGitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_paths: list[Path] = Field(
        default=[],
        description=(
            "Repos to scan for `gather git` activity. Each entry is a directory\n"
            "containing one or more git checkouts. Paths can be absolute or\n"
            "tilde-prefixed."
        ),
        json_schema_extra={
            "template_example": ["~/git"],
            "template_block_list": True,
        },
    )


class GatherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    git: GatherGitConfig = Field(default=GatherGitConfig(), description="")


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_driver: DailyDriverConfig = Field(
        default=DailyDriverConfig(),
        description="",
        json_schema_extra={"block_comment": "Required: workspace settings."},
    )
    user_profile: UserProfile = Field(
        default=UserProfile(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": "Optional: personal context surfaced into Claude prompts.",
        },
    )
    tracker: TrackerConfig = Field(
        description="",
        json_schema_extra={
            "block_comment": "Required: tracker configuration.",
            "template_default": TrackerConfig(  # type: ignore[dict-item]
                categories={"task": TrackerCategoryConfig(required=["title"])}
            ),
        },
    )
    recurring_tasks: list[RecurringTask] = Field(
        default=[],
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": "Optional: recurring tasks surfaced into day-start planning.",
            "template_example": [
                {
                    "name": "Review tracker follow-ups",
                    "cadence": "daily",
                    "estimated_minutes": 10,
                },
                {
                    "name": "Weekly retro",
                    "cadence": "weekly",
                    "day": "friday",
                },
            ],
            "template_example_inline_comments_by_index": {
                0: {"cadence": "daily | weekly | monthly"},  # type: ignore[dict-item]
                1: {"day": "only valid when cadence is weekly"},  # type: ignore[dict-item]
            },
        },
    )
    voice_profile: VoiceProfile = Field(
        default=VoiceProfile(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: voice profile hints used by `voice-update` and"
                " summary outputs."
            ),
        },
    )
    scheduler: dict[str, Any] | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: scheduler config (consumed by `scheduler install`).\n"
                "Freeform dict of per-job launchd cadence overrides for `check_in` and\n"
                "`jobs`. Each key sets `hour` and `minute` to override the built-in\n"
                "default. NOTE: `day_start` / `day_end` cadence does NOT live"
                " here — it\n"
                "lives in the separate top-level `schedule:` block below as HH:MM\n"
                "strings (that's the single source of truth for those two jobs)."
            ),
            "template_example": {
                "check_in": {"hour": 14, "minute": 0},
                "jobs": {"hour": 8, "minute": 30},
            },
        },
    )
    schedule: ScheduleConfig = Field(
        default=ScheduleConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: day-start / day-end scheduled times (HH:MM, 24h).\n"
                "When set, `scheduler install` adds launchd jobs that fire"
                " `day-start` /\n"
                "`day-end` at these times. Leave unset to skip the scheduled run."
                " This\n"
                "is also read by `is_late_day` evaluation — single source of truth."
            ),
        },
    )
    claude: ClaudeConfig = Field(
        default=ClaudeConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": "Optional: claude session behavior.",
        },
    )
    ai: AIConfig = Field(
        default=AIConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: AI provider routing for headless tasks (enrichment,"
                " summary).\n"
                "Interactive launchers (day-start, check-in, day-end) always use"
                " claude —\n"
                "they need session resume / agents / workspace context that Ollama"
                " lacks.\n"
                "Omitting the entire block keeps claude defaults for every task."
            ),
        },
    )
    focus: FocusConfig = Field(
        default=FocusConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": "Optional: focus mode defaults.",
        },
    )
    gather: GatherConfig = Field(
        default=GatherConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: gather config (controls what `daily-driver gather"
                " <kind>` reads)."
            ),
        },
    )
    plugins: PluginsConfig = Field(
        default=PluginsConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: plugins. Each plugin schema is strict — typos error out."
            ),
        },
    )
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: free-form namespace for user-only keys the program does\n"
                "not interpret. Use this for ad-hoc workspace tracking instead of\n"
                "adding keys at the config root (root is strict — typos error out)."
            ),
            "template_example": {
                "personal": {"motto": "ship it"},
            },
        },
    )
