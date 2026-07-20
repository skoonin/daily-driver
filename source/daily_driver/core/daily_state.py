"""Per-day state YAML for the day-cycle (day-start / check-in / day-end).

Each day's state lives at `<workspace.ephemeral_dir>/daily/YYYY-MM-DD.yaml`
(i.e. `<root>/.daily-driver/state/daily/YYYY-MM-DD.yaml`). Per-day filename
keeps midnight-rollover unambiguous and gives every day its own flock.

Durable read/write (shared-lock read, exclusive-lock atomic write via tempfile +
fsync + `os.replace`) is delegated to `core/yaml_store.py`, shared with
`core/session_pointer.py`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from daily_driver.core import clock, yaml_store
from daily_driver.core.workspace import Workspace

# 2 hours past schedule.day_start counts as "late". Hardcoded — YAGNI on a
# per-user knob until someone asks. Fallback (no schedule.day_start configured)
# is "after 11:00 absolute".
LATE_DAY_GRACE = timedelta(hours=2)
LATE_DAY_FALLBACK_TIME = time(11, 0)


class DailyStateError(RuntimeError):
    """Raised when a daily-state YAML on disk cannot be parsed."""


class DailyState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    last_day_start_session_id: str | None = None
    last_day_start_at: datetime | None = None
    last_check_in_at: datetime | None = None
    # F4 informational metadata: True when day-start ran more than
    # LATE_DAY_GRACE past the configured schedule.day_start (or after
    # LATE_DAY_FALLBACK_TIME absolute when no schedule is configured).
    late_day: bool = False


def state_path(workspace: Workspace, day: date) -> Path:
    """Return the per-day state YAML path. Pure; does not create parents."""
    return workspace.ephemeral_dir / "daily" / f"{day.isoformat()}.yaml"


def read_state(workspace: Workspace, day: date) -> DailyState | None:
    """Read the day's state YAML (None when absent).

    Raises DailyStateError (with the on-disk path) when the file exists but is
    unparseable or fails schema validation — surfaces actionable context to the
    user instead of a bare YAMLError / ValidationError. Hand-editing this file is
    unsupported but happens; the path makes the error recoverable.
    """
    return yaml_store.read_model(
        state_path(workspace, day), DailyState, DailyStateError
    )


def write_state(workspace: Workspace, state: DailyState) -> None:
    """Atomic + flock-guarded write of the day's state YAML."""
    yaml_store.write_model(state_path(workspace, state.date), state)


def is_late_day(workspace: Workspace, now: datetime | None = None) -> bool:
    """Return True iff the current time is past the late-day cutoff.

    Single source of truth: `config.schedule.day_start` (HH:MM). When set,
    the cutoff is `day_start + LATE_DAY_GRACE`. When unset, the cutoff is
    `LATE_DAY_FALLBACK_TIME` absolute.
    """
    current = now if now is not None else clock.now()
    schedule_day_start = workspace.config.schedule.day_start
    if schedule_day_start is None:
        return current.time() > LATE_DAY_FALLBACK_TIME
    hh, mm = schedule_day_start.split(":")
    scheduled = datetime.combine(
        current.date(), time(int(hh), int(mm)), tzinfo=current.tzinfo
    )
    return current > scheduled + LATE_DAY_GRACE


__all__ = [
    "DailyState",
    "DailyStateError",
    "LATE_DAY_FALLBACK_TIME",
    "LATE_DAY_GRACE",
    "is_late_day",
    "read_state",
    "state_path",
    "write_state",
]
