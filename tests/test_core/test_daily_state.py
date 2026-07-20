"""RED-phase tests for core/daily_state.py — module does not exist yet."""

from __future__ import annotations

import multiprocessing
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from daily_driver.core.workspace import Workspace


def _init_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    return ws


def _ws(tmp_path: Path) -> Workspace:
    root = _init_workspace(tmp_path)
    return Workspace.discover_or_fail(override=root)


# Module-level helper for multiprocessing spawn picklability (mirrors test_locking.py).
def _hold_state_lock(
    ws_root: Path,
    day_iso: str,
    ready_event,  # multiprocessing.Event
    done_event,  # multiprocessing.Event
) -> None:
    from daily_driver.core.daily_state import state_path
    from daily_driver.core.locking import file_lock
    from daily_driver.core.workspace import Workspace as _Workspace

    ws = _Workspace.discover_or_fail(override=ws_root)
    target = state_path(ws, date.fromisoformat(day_iso))
    lock_file = target.with_suffix(target.suffix + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(lock_file):
        ready_event.set()
        done_event.wait(timeout=10)


def test_state_path_resolves_under_ephemeral_daily(tmp_path: Path) -> None:
    from daily_driver.core.daily_state import state_path

    ws = _ws(tmp_path)
    day = date(2026, 5, 8)
    p = state_path(ws, day)

    assert p == ws.state_dir / "state" / "daily" / "2026-05-08.yaml"
    # state_path is a pure path computation; should not auto-create parents.
    assert not p.parent.exists()


def test_read_state_returns_none_when_absent(tmp_path: Path) -> None:
    from daily_driver.core.daily_state import read_state

    ws = _ws(tmp_path)
    assert read_state(ws, date(2026, 5, 8)) is None


def test_write_then_read_roundtrips_all_fields(tmp_path: Path) -> None:
    from daily_driver.core.daily_state import DailyState, read_state, write_state

    ws = _ws(tmp_path)
    sid = str(uuid.uuid4())
    started = datetime(2026, 5, 8, 9, 30, tzinfo=timezone.utc)
    checked = datetime(2026, 5, 8, 14, 15, tzinfo=timezone.utc)
    state = DailyState(
        date=date(2026, 5, 8),
        last_day_start_session_id=sid,
        last_day_start_at=started,
        last_check_in_at=checked,
    )
    write_state(ws, state)

    loaded = read_state(ws, date(2026, 5, 8))
    assert loaded == state
    assert loaded is not None
    assert loaded.last_day_start_at.tzinfo is not None
    assert loaded.last_check_in_at.tzinfo is not None


def test_write_then_read_roundtrips_default_none_fields(tmp_path: Path) -> None:
    from daily_driver.core.daily_state import DailyState, read_state, write_state

    ws = _ws(tmp_path)
    state = DailyState(date=date(2026, 5, 8))
    write_state(ws, state)
    loaded = read_state(ws, date(2026, 5, 8))

    assert loaded is not None
    assert loaded.last_day_start_session_id is None
    assert loaded.last_day_start_at is None
    assert loaded.last_check_in_at is None


def test_write_state_creates_parent_directory(tmp_path: Path) -> None:
    from daily_driver.core.daily_state import DailyState, state_path, write_state

    ws = _ws(tmp_path)
    daily_dir = ws.state_dir / "state" / "daily"
    assert not daily_dir.exists()

    write_state(ws, DailyState(date=date(2026, 5, 8)))

    target = state_path(ws, date(2026, 5, 8))
    assert target.exists()
    assert daily_dir.is_dir()


def test_write_state_atomic_no_tmp_left_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.core import yaml_store as ys_mod
    from daily_driver.core.daily_state import DailyState, state_path, write_state

    ws = _ws(tmp_path)
    target = state_path(ws, date(2026, 5, 8))
    target.parent.mkdir(parents=True, exist_ok=True)

    # Pre-existing content that must remain untouched on crash.
    original = "preserved: true\n"
    target.write_text(original, encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash mid-replace")

    # The atomic write now lives in yaml_store; patch os.replace there.
    monkeypatch.setattr(ys_mod.os, "replace", boom)

    with pytest.raises(OSError, match="simulated crash"):
        write_state(ws, DailyState(date=date(2026, 5, 8)))

    # Original survives; no .tmp lingers.
    assert target.read_text(encoding="utf-8") == original
    leftovers = [
        p for p in target.parent.iterdir() if p.name != target.name and ".tmp" in p.name
    ]
    assert leftovers == [], f"tempfiles leaked: {leftovers}"


def test_write_state_flock_blocks_concurrent_writer(tmp_path: Path) -> None:
    from daily_driver.core.daily_state import DailyState, write_state

    ws = _ws(tmp_path)
    day = date(2026, 5, 8)

    ready = multiprocessing.Event()
    done = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_hold_state_lock, args=(ws.root, day.isoformat(), ready, done)
    )
    proc.start()
    try:
        assert ready.wait(timeout=5), "child never acquired lock"

        # Spawn a writer in a thread; it must block while the lock is held.
        import threading

        finished = threading.Event()

        def writer() -> None:
            write_state(ws, DailyState(date=day))
            finished.set()

        t = threading.Thread(target=writer, daemon=True)
        t.start()

        # Confirm the writer is blocked: it should not finish quickly.
        assert not finished.wait(timeout=0.5), "writer did not block on lock"

        # Release the child's lock; writer should now complete.
        done.set()
        proc.join(timeout=5)
        assert finished.wait(timeout=5), "writer never completed after lock release"
        t.join(timeout=2)
    finally:
        done.set()
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)


def test_extra_fields_rejected(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from daily_driver.core.daily_state import DailyState

    with pytest.raises(ValidationError):
        DailyState(date=date(2026, 5, 8), bogus_field="nope")  # type: ignore[call-arg]


def test_session_id_accepts_uuid_string(tmp_path: Path) -> None:
    from daily_driver.core.daily_state import DailyState

    sid = str(uuid.uuid4())
    state = DailyState(date=date(2026, 5, 8), last_day_start_session_id=sid)
    assert state.last_day_start_session_id == sid


def test_read_state_raises_dailystate_error_with_path_on_corrupt_yaml(
    tmp_path: Path,
) -> None:
    from daily_driver.core.daily_state import (
        DailyStateError,
        read_state,
        state_path,
    )

    ws = _ws(tmp_path)
    target = state_path(ws, date(2026, 5, 8))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("date: 2026-05-08\n  bad: : :\n", encoding="utf-8")

    with pytest.raises(DailyStateError) as exc:
        read_state(ws, date(2026, 5, 8))
    assert str(target) in str(exc.value)


def test_read_state_raises_dailystate_error_on_top_level_non_mapping(
    tmp_path: Path,
) -> None:
    from daily_driver.core.daily_state import (
        DailyStateError,
        read_state,
        state_path,
    )

    ws = _ws(tmp_path)
    target = state_path(ws, date(2026, 5, 8))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- just a list\n- not a mapping\n", encoding="utf-8")

    with pytest.raises(DailyStateError) as exc:
        read_state(ws, date(2026, 5, 8))
    assert "mapping" in str(exc.value).lower()
    assert str(target) in str(exc.value)


def test_read_state_raises_dailystate_error_on_schema_violation(
    tmp_path: Path,
) -> None:
    from daily_driver.core.daily_state import (
        DailyStateError,
        read_state,
        state_path,
    )

    ws = _ws(tmp_path)
    target = state_path(ws, date(2026, 5, 8))
    target.parent.mkdir(parents=True, exist_ok=True)
    # Missing required `date` field.
    target.write_text("late_day: true\n", encoding="utf-8")

    with pytest.raises(DailyStateError) as exc:
        read_state(ws, date(2026, 5, 8))
    assert str(target) in str(exc.value)


def test_is_late_day_with_schedule_configured(tmp_path: Path) -> None:
    """F4: schedule.day_start='07:00' → late after 09:00 (07:00 + 2h grace).

    Also exercises the YAML-int coercion: unquoted `07:00` in YAML parses as
    int 420 (= 7 * 60); the field validator must round-trip it to '07:00'.
    """
    from datetime import datetime, timezone

    from daily_driver.core.daily_state import is_late_day

    ws_root = _init_workspace(tmp_path)
    cfg = ws_root / ".dd-config.yaml"
    cfg.write_text(
        cfg.read_text() + "\nschedule:\n  day_start: 07:00\n", encoding="utf-8"
    )
    ws = Workspace.discover_or_fail(override=ws_root)

    on_time = datetime(2026, 5, 8, 8, 30, tzinfo=timezone.utc)
    cutoff = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    late = datetime(2026, 5, 8, 9, 1, tzinfo=timezone.utc)

    assert is_late_day(ws, on_time) is False
    assert is_late_day(ws, cutoff) is False  # exactly at +2h grace, not yet late
    assert is_late_day(ws, late) is True


def test_is_late_day_fallback_no_schedule(tmp_path: Path) -> None:
    """F4: with no schedule.day_start configured, fallback is 11:00 absolute."""
    from datetime import datetime, timezone

    from daily_driver.core.daily_state import is_late_day

    ws = _ws(tmp_path)

    morning = datetime(2026, 5, 8, 10, 59, tzinfo=timezone.utc)
    cutoff = datetime(2026, 5, 8, 11, 0, tzinfo=timezone.utc)
    afternoon = datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc)

    assert is_late_day(ws, morning) is False
    assert is_late_day(ws, cutoff) is False
    assert is_late_day(ws, afternoon) is True


def test_state_yaml_is_human_readable(tmp_path: Path) -> None:
    from daily_driver.core.daily_state import DailyState, state_path, write_state

    ws = _ws(tmp_path)
    sid = str(uuid.uuid4())
    started = datetime(2026, 5, 8, 9, 30, tzinfo=timezone.utc)
    write_state(
        ws,
        DailyState(
            date=date(2026, 5, 8),
            last_day_start_session_id=sid,
            last_day_start_at=started,
        ),
    )

    target = state_path(ws, date(2026, 5, 8))
    raw = target.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)

    # Key presence — no brittle whole-string comparison.
    assert "date" in parsed
    assert "last_day_start_session_id" in parsed
    assert "last_day_start_at" in parsed
    assert "last_check_in_at" in parsed
    assert parsed["last_day_start_session_id"] == sid
