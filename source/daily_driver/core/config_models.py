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
            "Warn (stderr, one-time) when --status is outside the recommended set\n"
            "(open, in-progress, blocked, done, ruled-out). The entry still saves."
        ),
        json_schema_extra={"template_commented": True},
    )
    extra_statuses: list[str] = Field(
        default=[],
        description=(
            "Extra recommended statuses, added to the built-ins above. These never\n"
            "trigger warn_unknown_status. Spelling is normalized (case-folded,\n"
            "underscores -> hyphens), so `ruled_out` == `ruled-out`."
        ),
        json_schema_extra={
            "template_commented": True,
            "template_example": ["waiting", "snoozed"],
        },
    )
    terminal_statuses: list[str] = Field(
        default=[],
        description=(
            "Extra terminal statuses, merged with the built-ins (done, ruled-out,\n"
            "dropped, rejected, closed). Terminal entries are excluded from stalled\n"
            "detection and follow-ups. Built-ins cannot be removed."
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
                "Add more categories as needed. `required` lists fields that must\n"
                "be set on `tracker add`.\n"
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
    """Per-job launchd cadence for `check-in` and `jobs` runs.

    HH:MM strings, 24-hour clock; validated at `scheduler install` time by
    `scheduler._parse_hhmm`. Each job's `days` narrows which days it fires
    ("daily" default, "weekdays", or a list of day names). `day_start` /
    `day_end` cadence lives separately in `ScheduleConfig` (the single source
    of truth for those two jobs).
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
            "When true, `check-in` resumes the prior claude session instead of\n"
            "opening a fresh one."
        ),
    )


class AITaskConfig(BaseModel):
    """Provider + model selection for a single headless AI task."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] | None = Field(
        default=None,
        description=(
            "claude | ollama. Unset inherits the resolution chain (domain\n"
            "default -> ai.provider -> claude); set to pin this task."
        ),
        json_schema_extra={"template_example": "claude"},
    )
    model: str | None = Field(
        default=None,
        description=(
            'Provider-specific model id (claude: "sonnet"; ollama: a pulled tag\n'
            'like "qwen2.5:14b"). Chosen independently of provider, so it must\n'
            "match whichever provider this task resolves to."
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
        description=(
            'Claude model ("sonnet", "haiku", "opus"). Unset lets the claude CLI\n'
            "pick its default. A `--model` flag overrides this."
        ),
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
        description="Worker threads for parallel enrichment. 1 forces serial.",
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
        description=(
            "Global fallback provider for every routable task (summary,\n"
            "voice_update, job_search enrichment). Set to `ollama` to route the\n"
            "whole app in one line; per-task blocks still override."
        ),
    )
    model: str | None = Field(
        default=None,
        description=(
            "Global fallback model, used when neither the task block nor a domain\n"
            "default names one. Chosen independently of provider, so it must match\n"
            "whichever provider a task resolves to."
        ),
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )
    summary: AITaskConfig = Field(default=AITaskConfig(), description="")
    voice_update: AITaskConfig = Field(
        default=AITaskConfig(),
        description="Provider / model routing for the `voice-update` task.",
    )
    interactive: InteractiveAIConfig = Field(
        default=InteractiveAIConfig(),
        description=(
            "Interactive launchers (day-start, day-end, check-in) always run on\n"
            "claude, so this block is model-only (no provider knob). A `--model`\n"
            "flag overrides it."
        ),
    )
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
            "Fallback duration when `focus on` runs without `--for`\n"
            '(same suffixes as --for: "25m", "1h", "90m").'
        ),
        json_schema_extra={"template_quote": True},
    )


class GatherGitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_paths: list[Path] = Field(
        default=[],
        description=(
            "Directories to scan for git activity (absolute or tilde-prefixed).\n"
            "Each holds one or more git checkouts."
        ),
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
        description="Opt-in switch for `calendar sync`; false makes sync a clean no-op.",
    )
    plan_calendar_name: str = Field(
        default="Daily Plan",
        description="Local macOS Calendar to write into; must already exist in Calendar.app.",
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
                "Optional: check-in / jobs scheduler (consumed by `scheduler"
                " install`).\n"
                "`checkin.times` is a list of HH:MM; `jobs.time` is one HH:MM. Each\n"
                'job takes an optional `days`: "daily" (default), "weekdays", or a\n'
                "day list. day-start / day-end cadence lives in the separate\n"
                "`schedule:` block below."
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
                "Optional: day-start / day-end scheduled times (HH:MM, 24h). When"
                " set,\n"
                "`scheduler install` fires those jobs; `days` narrows both"
                ' ("weekdays"\n'
                "or a day list, default daily). Also the single source of truth for\n"
                "`is_late_day`. Leave unset to skip the scheduled run."
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
                "Optional: AI provider routing for headless tasks (summary,\n"
                "voice-update, job_search enrichment) plus the shared claude/ollama\n"
                "connection blocks. Interactive launchers (day-start, check-in,\n"
                "day-end) always use claude. Omit the block to keep claude"
                " defaults.\n"
                "\n"
                "Resolution chain for any task: per-task/phase -> domain default ->\n"
                "global `ai.provider` -> claude. Provider and model are chosen\n"
                "independently, so a model must be valid for whatever provider it\n"
                'lands on (claude: "sonnet"; ollama: a tag like "qwen2.5:14b").'
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
    calendar: CalendarConfig = Field(
        default=CalendarConfig(),
        description="",
        json_schema_extra={
            "template_commented": True,
            "block_comment": (
                "Optional: write daily-plan time blocks to a local macOS Calendar\n"
                "(consumed by `calendar sync`, macOS-only)."
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
