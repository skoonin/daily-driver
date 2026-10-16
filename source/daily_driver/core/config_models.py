from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# PluginsConfig is assembled in the plugins package from PLUGINS metadata, not
# here, so core need not hardcode plugin namespaces. Safe one-directional
# import: the plugins package and its config models never import back into core.
from daily_driver.plugins.config import PluginsConfig


class DailyDriverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: str = Field(
        default=".",
        description="Where the tracker, notes and other output go. Relative to here.",
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
        json_schema_extra={
            "inline_comment": '["US", "CA"]',
            "template_plugin": "job_search",
        },
    )
    work_auth: dict[str, str] = Field(
        default={},
        description="",
        json_schema_extra={
            "inline_comment": '{"US": "citizen", "CA": "PR"}',
            "template_plugin": "job_search",
        },
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
            "template_plugin": "job_search",
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


class TrackerCategoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: list[str] = Field(default=[], description="")


class TrackerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_category: str = Field(default="task", description="")
    warn_unknown_status: bool = Field(
        default=True,
        description=(
            "Warn when --status is not a usual one for its category (usually\n"
            "open, in-progress, blocked, done, ruled-out). It still saves."
        ),
        json_schema_extra={"template_commented": True},
    )
    extra_statuses: list[str] = Field(
        default=[],
        description="More statuses that do not warn.",
        json_schema_extra={
            "template_commented": True,
            "template_example": ["waiting", "snoozed"],
        },
    )
    terminal_statuses: list[str] = Field(
        default=[],
        description=(
            "More statuses that close an entry, added to done, ruled-out,\n"
            "dropped, rejected and closed. Closed entries get no follow-ups."
        ),
        json_schema_extra={
            "template_commented": True,
            "template_example": ["cancelled"],
        },
    )
    categories: dict[str, TrackerCategoryConfig] = Field(
        default={"task": TrackerCategoryConfig(required=["title"])},
        description="",
        json_schema_extra={
            "trailing_comment": (
                "More categories. `required` lists the fields `tracker add` needs.\n"
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


_HHMM_RE = re.compile(r"^([0-1]?\d|2[0-3]):([0-5]\d)$")

# Canonical three-letter day names, ordered to match launchd's Weekday
# integers (0 = Sunday). `days` fields normalize to these.
_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_DAYS_SHORTCUTS = ("daily", "weekdays")


def _normalize_days(v: Any) -> Any:
    """Normalize a `days` cadence value.

    Accepts the shortcut strings "daily" / "weekdays", or a list of day names
    (full or abbreviated, any case) normalized to canonical three-letter
    lowercase. An explicit empty list is rejected — omit the key (or use
    "daily") to fire every day.
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
            raise ValueError("days list must not be empty; omit for daily")
        return normalized
    raise ValueError(f"expected a days shortcut string or list, got {v!r}")


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
    days: str | list[str] = Field(
        default="daily",
        description="",
        json_schema_extra={"template_example": "weekdays", "template_quote": True},
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

    @field_validator("days", mode="before")
    @classmethod
    def _validate_days(cls, v: Any) -> Any:
        return _normalize_days(v)


class CheckinSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    times: list[str] = Field(
        default_factory=list,
        description="",
        json_schema_extra={
            "template_example": ["11:00", "15:00"],
            "template_block_list": True,
        },
    )
    days: str | list[str] = Field(
        default="daily",
        description="",
        json_schema_extra={"template_example": "weekdays", "template_quote": True},
    )

    @field_validator("days", mode="before")
    @classmethod
    def _validate_days(cls, v: Any) -> Any:
        return _normalize_days(v)


class JobSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "07:00", "template_quote": True},
    )
    days: str | list[str] = Field(
        default="daily",
        description="",
        json_schema_extra={"template_example": ["sun", "wed"]},
    )

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

    checkin: CheckinSchedule | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example_model": True},
    )
    jobs: JobSchedule | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_example_model": True,
            "template_plugin": "job_search",
        },
    )
    # Explicit template_example rather than template_example_model: discovery
    # shares the JobSchedule class with `jobs`, whose own field-level
    # examples (07:00, [sun, wed]) belong to `jobs`. Reusing them here would
    # render discovery's scaffolded example with jobs's schedule instead of
    # discovery's real default (23:59, [sat, tue]).
    discovery: JobSchedule | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_example": {"time": "23:59", "days": ["sat", "tue"]},
            "template_plugin": "job_search",
        },
    )


class ClaudeConfig(BaseModel):
    """Knobs for how daily-driver invokes the `claude` CLI."""

    model_config = ConfigDict(extra="forbid")

    resume_check_in: bool = Field(
        default=False,
        description="true makes `check-in` reopen the latest Claude session.",
    )


class AITaskConfig(BaseModel):
    """Provider + model selection for a single headless AI task."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] | None = Field(
        default=None,
        description="claude or ollama. Unset uses `ai.provider`.",
        json_schema_extra={"template_example": "claude"},
    )
    # Provider and model resolve separately (ai_provider.resolve_route), so an
    # inherited `ai.model` meant for the other provider fails at call time.
    model: str | None = Field(
        default=None,
        description=(
            "Model for this task. Unset uses `ai.model`, so set it when\n"
            "`ai.model` is set and this task uses the other provider."
        ),
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )


class OllamaConfig(BaseModel):
    """Settings consulted when a task is routed to the ollama provider."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(default="http://localhost:11434", description="")
    timeout: int = Field(
        default=60,
        description="Seconds per call. Ollama is slower than the Claude API.",
    )
    max_parallel: int = Field(
        default=4,
        ge=1,
        description="Job search enrichment calls at once. 1 runs them one at a time.",
        json_schema_extra={"template_plugin": "job_search"},
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

    model: str | None = Field(
        default=None,
        description="sonnet, haiku or opus. Unset uses the claude CLI default.",
        # Rendered active (not commented) as the block's sole field, so the
        # generated example parses as a mapping rather than an empty key.
        json_schema_extra={"template_example": "sonnet"},
    )


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
        description="Job search enrichment calls at once. 1 runs them one at a time.",
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

    provider: Literal["claude", "ollama"] = Field(
        default="claude",
        description="Default provider for every task: claude or ollama.",
    )
    model: str | None = Field(
        default=None,
        description=(
            'Default model. It must suit the provider (claude: "sonnet";\n'
            'ollama: a tag like "qwen2.5:14b").'
        ),
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )
    summary: AITaskConfig = Field(
        default=AITaskConfig(), description="The `summary` task."
    )
    voice_update: AITaskConfig = Field(
        default=AITaskConfig(),
        description="The `voice-update` task.",
    )
    interactive: InteractiveAIConfig = Field(
        default=InteractiveAIConfig(),
        description=(
            "day-start, check-in and day-end. They always use Claude;\n"
            "`--model` overrides this."
        ),
    )
    # Only job search enrichment reads this block (its sole setting is
    # max_parallel), so a plain workspace config leaves it out.
    claude: ClaudeProviderConfig = Field(
        default=ClaudeProviderConfig(),
        description="Used when a task runs on claude.",
        json_schema_extra={"template_plugin": "job_search"},
    )
    ollama: OllamaConfig = Field(
        default=OllamaConfig(),
        description="Used when a task runs on ollama.",
    )


class FocusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_duration: str = Field(
        default="25m",
        description='Length of `focus on` without `--for` ("25m", "1h", "90m").',
        json_schema_extra={"template_quote": True},
    )


class GatherGitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_paths: list[Path] = Field(
        default=[],
        description="Folders that hold your git checkouts.",
        json_schema_extra={
            "template_example": ["~/git"],
            "template_block_list": True,
        },
    )


class GatherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    git: GatherGitConfig = Field(default=GatherGitConfig(), description="")


class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_enabled: bool = Field(
        default=False,
        description="false makes `calendar sync` do nothing.",
    )
    plan_calendar_name: str = Field(
        default="Daily Plan",
        description="Calendar to write to. It must already exist.",
        json_schema_extra={"template_quote": True},
    )


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
    - Narrative / free-form user context: ``voice-profile.md`` in the
      workspace, not the YAML config.
    """

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
            "block_comment": "Optional: about you, for Claude prompts.",
        },
    )
    tracker: TrackerConfig = Field(
        description="",
        json_schema_extra={
            "block_comment": "Required: tracker settings.",
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
            "block_comment": "Optional: repeating tasks, as context for Claude sessions.",
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
    scheduler: SchedulerConfig | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: background runs set up by `scheduler install`. day-start\n"
                "and day-end times are in `schedule:` below.\n"
                'Times are HH:MM; `days` is "daily" (default), "weekdays" or a list.'
            ),
            "template_example_model": True,
        },
    )
    schedule: ScheduleConfig = Field(
        default=ScheduleConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: day-start and day-end times (HH:MM) for scheduled runs.\n"
                "A day-start over 2 hours after `day_start` is a late start."
            ),
        },
    )
    claude: ClaudeConfig = Field(
        default=ClaudeConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": "Optional: Claude session behavior.",
        },
    )
    ai: AIConfig = Field(
        default=AIConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: AI provider and model per task. Leave out to use Claude."
            ),
        },
    )
    focus: FocusConfig = Field(
        default=FocusConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": "Optional: focus mode.",
        },
    )
    gather: GatherConfig = Field(
        default=GatherConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": "Optional: what `daily-driver gather` reads.",
        },
    )
    calendar: CalendarConfig = Field(
        default=CalendarConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: copy plan time blocks to a macOS Calendar (`calendar sync`)."
            ),
        },
    )
    plugins: PluginsConfig = Field(
        default=PluginsConfig(),
        description="",
        json_schema_extra={"template_plugins_section": True},
    )

    @field_validator("plugins", mode="before")
    @classmethod
    def _empty_plugins_section(cls, value: Any) -> Any:
        # `plugins:` with every plugin block commented out loads as null, a
        # natural way to switch every plugin off; treat it as no plugins.
        return {} if value is None else value
