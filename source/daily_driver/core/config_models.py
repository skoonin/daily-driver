from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# PluginsConfig is assembled in the plugins package from PLUGINS metadata, not
# here, so core need not hardcode plugin namespaces. Safe one-directional
# import: the plugins package and its config models never import back into core.
from daily_driver.plugins.config import PluginsConfig


class DailyDriverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: str = "."


class TrackerCategoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: list[str] = []


class TrackerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_category: str = "task"
    warn_unknown_status: bool = True
    extra_statuses: list[str] = []
    terminal_statuses: list[str] = []
    categories: dict[str, TrackerCategoryConfig] = {
        "task": TrackerCategoryConfig(required=["title"])
    }

    @model_validator(mode="after")
    def _default_category_in_categories(self) -> TrackerConfig:
        if self.default_category not in self.categories:
            raise ValueError(
                f"default_category '{self.default_category}' must be a key in categories"
            )
        return self


_HHMM_RE = re.compile(r"^([0-1]?\d|2[0-3]):([0-5]\d)$")

# Canonical three-letter day names, ordered to match launchd's Weekday
# integers (0 = Sunday). `days` fields normalize to these.
_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_DAYS_SHORTCUTS = ("daily", "weekdays")


def _normalize_days(v: Any) -> Any:
    """Normalize a `days` cadence value.

    Accepts the shortcut strings "daily" / "weekdays", or a list of day names
    (full or abbreviated, any case) normalized to canonical three-letter
    lowercase. An explicit empty list is rejected — use "daily" to fire every
    day.
    """
    if v is None:
        return v
    if isinstance(v, str):
        shortcut = v.strip().lower()
        if shortcut in _DAYS_SHORTCUTS:
            return shortcut
        raise ValueError(
            f"days must be {_DAYS_SHORTCUTS[0]!r}, {_DAYS_SHORTCUTS[1]!r},"
            f" or a list of day names, got {v!r}"
        )
    if isinstance(v, list):
        normalized: list[str] = []
        for item in v:
            name = str(item).strip().lower()[:3]
            if name not in _DAY_NAMES:
                raise ValueError(f"unknown day name: {item!r}")
            if name not in normalized:
                normalized.append(name)
        if not normalized:
            raise ValueError("days list must not be empty; use daily")
        return normalized
    raise ValueError(f"expected a days shortcut string or list, got {v!r}")


class ScheduleConfig(BaseModel):
    """Single source of truth for daily-driver's scheduled times.

    Drives BOTH the launchd plist install (`scheduler install` builds plists
    from these) AND `is_late_day` evaluation (so prompts can frame the agenda
    when the user starts late). HH:MM strings; 24-hour clock.
    """

    model_config = ConfigDict(extra="forbid")

    day_start: str | None = None
    day_end: str | None = None
    days: str | list[str] = "daily"

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

    @field_validator("days", mode="before")
    @classmethod
    def _validate_days(cls, v: Any) -> Any:
        return _normalize_days(v)


class CheckinSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    times: list[str] = Field(
        default_factory=list,
    )
    days: str | list[str] = "daily"

    @field_validator("days", mode="before")
    @classmethod
    def _validate_days(cls, v: Any) -> Any:
        return _normalize_days(v)


class JobSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: str | None = None
    days: str | list[str] = "daily"

    @field_validator("days", mode="before")
    @classmethod
    def _validate_days(cls, v: Any) -> Any:
        return _normalize_days(v)


class SchedulerConfig(BaseModel):
    """Per-job launchd cadence for `check-in`, `jobs`, and board discovery.

    HH:MM strings, 24-hour clock; validated at `scheduler install` time by
    `scheduler._parse_hhmm`. Each job's `days` narrows which days it fires
    ("daily" default, "weekdays", or a list of day names). `day_start` /
    `day_end` cadence lives separately in `ScheduleConfig` (the single source
    of truth for those two jobs).

    `discovery` is its own job rather than part of `jobs`: the board universe
    changes far more slowly than postings do, launchd takes a single command
    (so chaining would need a wrapper), and a sweep that hangs must not block
    the scrape.
    """

    model_config = ConfigDict(extra="forbid")

    checkin: CheckinSchedule | None = None
    jobs: JobSchedule | None = None
    discovery: JobSchedule | None = None


class ClaudeConfig(BaseModel):
    """Knobs for how daily-driver invokes the `claude` CLI."""

    model_config = ConfigDict(extra="forbid")

    resume_check_in: bool = False


class AITaskConfig(BaseModel):
    """Provider + model selection for a single headless AI task."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] | None = None
    # Provider and model resolve separately (ai_provider.resolve_route), so an
    # inherited `ai.model` meant for the other provider fails at call time.
    model: str | None = None


class OllamaConfig(BaseModel):
    """Settings consulted when a task is routed to the ollama provider."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = "http://localhost:11434"
    timeout: int = 60
    max_parallel: int = Field(
        default=4,
        ge=1,
    )


class InteractiveAIConfig(BaseModel):
    """Model selection for the claude-only interactive launchers.

    Distinct from AITaskConfig (which carries a `provider`): the interactive
    launchers always run on the claude CLI, so a `provider` here would be inert.
    Omitting it from the model — with `extra="forbid"` — means a stray
    `ai.interactive.provider:` is rejected loudly at config-load rather than
    accepted and silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = None


class ClaudeProviderConfig(BaseModel):
    """Settings consulted when a task is routed to the claude provider.

    Distinct from ClaudeConfig (top-level `claude:` session behavior) — this is
    the `ai.claude:` block parallel to `ai.ollama:`.
    """

    model_config = ConfigDict(extra="forbid")

    max_parallel: int = Field(
        default=4,
        ge=1,
        # Kept modest: claude is a rate-limited API reached through a CLI
        # subprocess per call, so wide fan-out trades throughput for throttling
        # and process overhead rather than the RAM ceiling ollama hits.
    )


class AIConfig(BaseModel):
    """Provider routing for the core `summary` task plus shared provider blocks.

    Default resolves to `provider: claude`, so omitting the `ai:` block in
    `.dd-config.yaml` preserves the existing claude-only behavior. The
    `claude:` / `ollama:` blocks are shared connection/tuning infra consulted
    by whichever provider a task routes to — `summary` here, and the
    job_search plugin's own `enrichment` routing
    (`plugins.job_search.enrichment.provider`). Interactive launchers
    (day-start, check-in, day-end) ignore this block — they always use the
    `claude` CLI directly for session / agent / workspace-context features
    Ollama doesn't have.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] = "claude"
    model: str | None = None
    summary: AITaskConfig = AITaskConfig()
    voice_update: AITaskConfig = AITaskConfig()
    interactive: InteractiveAIConfig = InteractiveAIConfig()
    # Only job search enrichment reads this block (its sole setting is
    # max_parallel), so a plain workspace config leaves it out.
    claude: ClaudeProviderConfig = ClaudeProviderConfig()
    ollama: OllamaConfig = OllamaConfig()


class FocusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_duration: str = "25m"


class GatherGitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_paths: list[Path] = []


class GatherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    git: GatherGitConfig = GatherGitConfig()


class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_enabled: bool = False
    plan_calendar_name: str = "Daily Plan"


class Config(BaseModel):
    """Root daily-driver config schema.

    Strict at the root: unknown top-level keys raise pydantic
    ``ValidationError`` so typos like ``tracer:`` for ``tracker:`` fail loudly
    at parse time instead of being silently ignored.

    User extensions belong on one of the existing seams, not on freeform root
    keys:

    - Per-entry user data: a tracker entry's ``extras`` map (set via
      ``daily-driver tracker add --extra key=value``), not a root config key.
    - Per-category required fields and custom categories:
      ``tracker.categories.<name>``.
    - Narrative / free-form user context, including recurring tasks:
      ``context.md`` and ``voice-profile.md`` in the workspace, not the YAML
      config.
    """

    model_config = ConfigDict(extra="forbid")

    daily_driver: DailyDriverConfig = DailyDriverConfig()
    tracker: TrackerConfig
    scheduler: SchedulerConfig | None = None
    schedule: ScheduleConfig = ScheduleConfig()
    claude: ClaudeConfig = ClaudeConfig()
    ai: AIConfig = AIConfig()
    focus: FocusConfig = FocusConfig()
    gather: GatherConfig = GatherConfig()
    calendar: CalendarConfig = CalendarConfig()
    plugins: PluginsConfig = PluginsConfig()

    @field_validator("plugins", mode="before")
    @classmethod
    def _empty_plugins_section(cls, value: Any) -> Any:
        # `plugins:` with every plugin block commented out loads as null, a
        # natural way to switch every plugin off; treat it as no plugins.
        return {} if value is None else value
