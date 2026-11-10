"""Time and day fields shared by core's and plugins' schedule settings.

Plugin config models import this module, and core's config models import the
plugins package, so it must not import any other daily_driver module.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

_HHMM_RE = re.compile(r"^([0-1]?\d|2[0-3]):([0-5]\d)$")

# Canonical three-letter day names, ordered to match launchd's Weekday
# integers (0 = Sunday). `days` fields normalize to these.
_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_DAYS_SHORTCUTS = ("daily", "weekdays")


def normalize_days(v: Any) -> Any:
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


def normalize_hhmm(v: Any) -> Any:
    if v is None:
        return v
    # YAML 1.1 reads an unquoted 10:30 as the base-60 integer 630. Its hour
    # never starts with 0, so a real time arrives as 60 or more; a smaller
    # integer such as `time: 9`, or a bool, is not a time.
    if isinstance(v, int) and not isinstance(v, bool) and v >= 60:
        hh, mm = divmod(v, 60)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            v = f"{hh:02d}:{mm:02d}"
    if not isinstance(v, str) or not _HHMM_RE.match(v):
        raise ValueError(f"expected HH:MM time string, got {v!r}")
    return v


class ScheduledRun(BaseModel):
    """One timed launchd run. A plugin subclasses it to give its runs defaults."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    time: str
    days: str | list[str] = "daily"

    @field_validator("time", mode="before")
    @classmethod
    def _validate_hhmm(cls, v: Any) -> Any:
        return normalize_hhmm(v)

    @field_validator("days", mode="before")
    @classmethod
    def _validate_days(cls, v: Any) -> Any:
        return normalize_days(v)
