"""Per-day state YAML for the day-cycle (day-start / check-in / day-end).

Each day's state lives at `<workspace.ephemeral_dir>/daily/YYYY-MM-DD.yaml`
(i.e. `<root>/.daily-driver/state/daily/YYYY-MM-DD.yaml`). Per-day filename
keeps midnight-rollover unambiguous and gives every day its own flock.

Atomic writes mirror the pattern in `core/voice.py:apply_update`: tempfile
in the same directory, fsync, then `os.replace`. Concurrent writers are
serialized via `core/locking.py:file_lock` on a sibling `.lock` file.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from daily_driver.core import clock
from daily_driver.core.locking import file_lock
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
    plan_summary: str = ""
    # F4 informational metadata: True when day-start ran more than
    # LATE_DAY_GRACE past the configured schedule.day_start (or after
    # LATE_DAY_FALLBACK_TIME absolute when no schedule is configured).
    late_day: bool = False


def state_path(workspace: Workspace, day: date) -> Path:
    """Return the per-day state YAML path. Pure; does not create parents."""
    return workspace.ephemeral_dir / "daily" / f"{day.isoformat()}.yaml"


def _lock_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".lock")


def read_state(workspace: Workspace, day: date) -> DailyState | None:
    """Read the day's state YAML.

    Returns None when the file is absent. Raises DailyStateError (with the
    on-disk path) when the file exists but is unparseable or fails schema
    validation — surfaces actionable context to the user instead of a bare
    YAMLError / ValidationError. Hand-editing this file is unsupported but
    happens; the path makes the error recoverable.
    """
    target = state_path(workspace, day)
    with file_lock(_lock_path(target), shared=True):
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise DailyStateError(f"{target}: invalid YAML: {exc}") from exc
    if data is None:
        return None
    if not isinstance(data, dict):
        raise DailyStateError(
            f"{target}: expected a YAML mapping at the top level, got {type(data).__name__}"
        )
    try:
        return DailyState.model_validate(data)
    except ValidationError as exc:
        raise DailyStateError(f"{target}: schema validation failed: {exc}") from exc


def write_state(workspace: Workspace, state: DailyState) -> None:
    """Atomic + flock-guarded write of the day's state YAML."""
    target = state_path(workspace, state.date)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = state.model_dump(mode="json")
    serialized = yaml.safe_dump(payload, sort_keys=False)

    with file_lock(_lock_path(target)):
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


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
