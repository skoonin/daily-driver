from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    # Warn (don't reject) when `tracker add/update --status` uses a value
    # outside the recommended set. Free-form is intentional; this is a nudge.
    warn_unknown_status: bool = True

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


class JobsConfig(BaseModel):
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


class JobspyToggle(SourceToggle):
    """Per-site enable flags for the JobSpy aggregator."""

    linkedin: bool = True
    indeed: bool = True
    google: bool = True


class ScraperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
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
    jobs: JobsConfig = JobsConfig()
    playwright_delays: dict[str, PlaywrightDelays] = {}
    sources: dict[str, SourceToggle] = {}
    parallel_workers: int = 4
    max_pages: int = 3
    max_retries: int = 3
    max_age_days: int = 30

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, v: Any) -> Any:
        # Migrate legacy `sources: {linkedin: true}` form to the typed
        # SourceToggle model. Accepts bool, dict, or already-typed instances.
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

    persona: str | None = None
    locations: Locations | None = None
    compensation: Compensation | None = None
    role_filters: RoleFilters | None = None
    roles: list[str] = []
    domain_keywords: list[str] = []
    seniority_keywords: list[str] = []
    min_comp_usd: int = 180000
    # When set, drop scraped jobs whose parsed comp currency doesn't match.
    # Unparseable comp is kept (None=sentinel for "couldn't read"). Decoupled
    # from compensation.currency: that one drives the user's pay-floor input;
    # this one prunes the input stream by source-currency.
    primary_currency: Literal["USD", "CAD", "EUR", "GBP"] | None = None
    scraper: ScraperConfig = ScraperConfig()
    sources: dict[str, Any] = {}


class PluginsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_search: JobSearchPlugin | None = None


_HHMM_RE = re.compile(r"^([0-1]?\d|2[0-3]):([0-5]\d)$")


class ScheduleConfig(BaseModel):
    """Single source of truth for daily-driver's scheduled times.

    Drives BOTH the launchd plist install (`scheduler install` builds plists
    from these) AND `is_late_day` evaluation (so prompts can frame the agenda
    when the user starts late). HH:MM strings; 24-hour clock.
    """

    model_config = ConfigDict(extra="forbid")

    day_start: str | None = None
    day_end: str | None = None

    @field_validator("day_start", "day_end", mode="before")
    @classmethod
    def _validate_hhmm(cls, v: Any) -> Any:
        if v is None:
            return v
        # PyYAML pitfall: unquoted `HH:MM` parses as base-60 int (e.g. 17:30 → 1050).
        # Round-trip integers back to "HH:MM" so users don't have to remember
        # to quote times in .dd-config.yaml.
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

    # When True, /check-in tries to --resume the day-start session UUID stored
    # in <workspace>/.daily-driver/state/daily/<date>.yaml. Defaults False per
    # plan §6 WS-Daily F3 — flip after measuring context-window growth across
    # a typical day, or per-user once flipped.
    resume_check_in: bool = False


class AITaskConfig(BaseModel):
    """Provider + model selection for a single headless AI task."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] = "claude"
    # Provider-specific model identifier. For claude: "sonnet", "haiku", etc.;
    # for ollama: a pulled tag like "qwen2.5:14b". None lets the provider pick
    # its own default.
    model: str | None = None


class OllamaConfig(BaseModel):
    """Settings consulted when a task is routed to the ollama provider."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = "http://localhost:11434"
    # Ollama is materially slower than the claude API; give it room.
    timeout: int = 60
    # Worker count for parallel enrichment. Default mirrors Ollama's own
    # OLLAMA_NUM_PARALLEL=4 default; raise the server-side env var first
    # if going above 4. Set to 1 to force serial behavior. No upper bound
    # enforced — Ollama queues to OLLAMA_MAX_QUEUE so over-subscription
    # degrades to slow, not crash, but each parallel call costs ~model-size
    # of RAM (KV cache).
    max_parallel: int = Field(default=4, ge=1)


class AIConfig(BaseModel):
    """Per-task provider routing for headless AI calls.

    Default resolves to `provider: claude` for every task, so omitting the
    `ai:` block in `.dd-config.yaml` preserves the existing claude-only
    behavior. Interactive launchers (day-start, check-in, day-end) ignore
    this block — they always use the `claude` CLI directly for session /
    agent / workspace-context features Ollama doesn't have.
    """

    model_config = ConfigDict(extra="forbid")

    enrichment: AITaskConfig = AITaskConfig()
    summary: AITaskConfig = AITaskConfig()
    ollama: OllamaConfig = OllamaConfig()


class FocusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Used when `daily-driver focus on` is invoked without `--for`.
    # Same syntax as `--for`: 30m, 2h, 1h30m, or bare minutes.
    default_duration: str = "25m"


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
    claude: ClaudeConfig = ClaudeConfig()
    ai: AIConfig = AIConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    focus: FocusConfig = FocusConfig()
    plugins: PluginsConfig = PluginsConfig()
