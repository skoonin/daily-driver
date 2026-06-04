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


class JobSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "07:00", "template_quote": True},
    )


class SchedulerConfig(BaseModel):
    """Per-job launchd cadence for `check-in` and `jobs` runs.

    HH:MM strings, 24-hour clock; validated at `scheduler install` time by
    `scheduler._parse_hhmm`. `day_start` / `day_end` cadence lives separately
    in `ScheduleConfig` (the single source of truth for those two jobs).
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
        json_schema_extra={"template_example_model": True},
    )


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
    max_parallel: int = Field(default=4, ge=1, description="")


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
        description="Worker threads for parallel enrichment. 1 forces serial.",
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
    claude: ClaudeProviderConfig = Field(
        default=ClaudeProviderConfig(),
        description="Only consulted when a task is routed to the claude provider.",
    )
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
    scheduler: SchedulerConfig | None = Field(
        default=None,
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: scheduler config (consumed by `scheduler install`).\n"
                "`checkin.times` is a list of HH:MM strings; `jobs.time` is a single\n"
                "HH:MM string. Unknown keys are rejected. NOTE: `day_start` /\n"
                "`day_end` cadence does NOT live here — it lives in the separate\n"
                "top-level `schedule:` block below as HH:MM strings (the single\n"
                "source of truth for those two jobs)."
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
