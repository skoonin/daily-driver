from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from daily_driver.core.providers import ProviderConfig, validate_routes
from daily_driver.core.schedule_fields import normalize_days, normalize_hhmm

# PluginsConfig is assembled in the plugins package from PLUGINS metadata, not
# here, so core need not hardcode plugin namespaces. Safe: the only core module
# plugin config models import is core.schedule_fields, which imports no other
# daily_driver module.
from daily_driver.plugins.config import PluginsConfig


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
        return normalize_hhmm(v)

    @field_validator("days", mode="before")
    @classmethod
    def _validate_days(cls, v: Any) -> Any:
        return normalize_days(v)


class CheckinSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    times: list[str] = Field(
        default_factory=list,
    )
    days: str | list[str] = "daily"

    @field_validator("days", mode="before")
    @classmethod
    def _validate_days(cls, v: Any) -> Any:
        return normalize_days(v)


class SchedulerConfig(BaseModel):
    """Launchd cadence for `check-in`.

    HH:MM strings, 24-hour clock; validated at `scheduler install` time by
    `scheduler._parse_hhmm`. `days` narrows which days it fires ("daily",
    "weekdays", or a list of day names). A line left unset takes its value from
    resources/templates/scheduler.default.yaml, not from the field default here
    (`scheduler._merge_scheduler_config`). `day_start` / `day_end` cadence lives
    separately in `ScheduleConfig` (the single source of truth for those two
    jobs). A plugin's runs live in the plugin's own settings.
    """

    model_config = ConfigDict(extra="forbid")

    checkin: CheckinSchedule | None = None


class InteractiveAIConfig(BaseModel):
    """Behaviour of the interactive launchers, whichever provider runs them."""

    model_config = ConfigDict(extra="forbid")

    # check-in continues the last session instead of starting a new one.
    resume_check_in: bool = False


class AIConfig(BaseModel):
    """AI providers and the settings of the interactive launchers.

    `providers` entries merge over the built-in providers (claude, opencode,
    lmstudio, ollama); see core.providers. With no `ai:` block every task
    runs on the built-in claude.
    """

    model_config = ConfigDict(extra="forbid")

    interactive: InteractiveAIConfig = InteractiveAIConfig()
    providers: dict[str, ProviderConfig] = {}

    @field_validator("providers", mode="before")
    @classmethod
    def _empty_providers(cls, value: Any) -> Any:
        # `providers:` or `opencode:` with every line commented out loads as
        # null; treat it as no entries, or an entry that changes nothing.
        if value is None:
            return {}
        if isinstance(value, dict):
            return {
                name: {} if entry is None else entry for name, entry in value.items()
            }
        return value


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

    tracker: TrackerConfig
    scheduler: SchedulerConfig | None = None
    schedule: ScheduleConfig = ScheduleConfig()
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

    @model_validator(mode="after")
    def _routes_resolve(self) -> Config:
        # Here, not on AIConfig: plugin routes live under `plugins`, which
        # AIConfig cannot see.
        validate_routes(self)
        return self
