"""Tests for core/session_pointer.py — the workspace-level session pointer."""

from __future__ import annotations

import multiprocessing
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_driver.core.workspace import Workspace


def _init_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    return ws


def _ws(tmp_path: Path) -> Workspace:
    root = _init_workspace(tmp_path)
    return Workspace.discover_or_fail(override=root)


# Module-level helper for multiprocessing spawn picklability (mirrors test_daily_state.py).
def _hold_pointer_lock(
    ws_root: Path,
    ready_event,  # multiprocessing.Event
    done_event,  # multiprocessing.Event
) -> None:
    from daily_driver.core.locking import file_lock
    from daily_driver.core.session_pointer import pointer_path
    from daily_driver.core.workspace import Workspace as _Workspace

    ws = _Workspace.discover_or_fail(override=ws_root)
    target = pointer_path(ws)
    lock_file = target.with_suffix(target.suffix + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(lock_file):
        ready_event.set()
        done_event.wait(timeout=10)


def test_pointer_path_resolves_under_ephemeral(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import pointer_path

    ws = _ws(tmp_path)
    p = pointer_path(ws)

    assert p == ws.state_dir / "state" / "session.yaml"


def test_read_pointer_returns_none_when_absent(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import read_pointer

    ws = _ws(tmp_path)
    assert read_pointer(ws) is None


def test_write_then_read_roundtrips_all_fields(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import (
        SessionPointer,
        read_pointer,
        write_pointer,
    )

    ws = _ws(tmp_path)
    sid = str(uuid.uuid4())
    started = datetime(2026, 5, 8, 9, 30, tzinfo=timezone.utc)
    pointer = SessionPointer(last_session_id=sid, last_session_at=started)
    write_pointer(ws, pointer)

    loaded = read_pointer(ws)
    assert loaded == pointer
    assert loaded is not None
    assert loaded.last_session_at is not None
    assert loaded.last_session_at.tzinfo is not None


def test_write_then_read_roundtrips_default_none_fields(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import (
        SessionPointer,
        read_pointer,
        write_pointer,
    )

    ws = _ws(tmp_path)
    write_pointer(ws, SessionPointer())
    loaded = read_pointer(ws)

    assert loaded is not None
    assert loaded.last_session_id is None
    assert loaded.last_session_at is None


def test_write_pointer_creates_parent_directory(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import (
        SessionPointer,
        pointer_path,
        write_pointer,
    )

    ws = _ws(tmp_path)
    target = pointer_path(ws)
    # Fresh workspace init creates the state dir; the pointer file itself is absent.
    assert not target.exists()

    write_pointer(ws, SessionPointer(last_session_id=str(uuid.uuid4())))

    assert target.exists()
    assert target.parent.is_dir()


def test_write_pointer_atomic_no_tmp_left_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.core import yaml_store as ys_mod
    from daily_driver.core.session_pointer import (
        SessionPointer,
        pointer_path,
        write_pointer,
    )

    ws = _ws(tmp_path)
    target = pointer_path(ws)
    target.parent.mkdir(parents=True, exist_ok=True)

    original = "last_session_id: preserved\n"
    target.write_text(original, encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash mid-replace")

    # The atomic write now lives in yaml_store; patch os.replace there.
    monkeypatch.setattr(ys_mod.os, "replace", boom)

    with pytest.raises(OSError, match="simulated crash"):
        write_pointer(ws, SessionPointer(last_session_id=str(uuid.uuid4())))

    assert target.read_text(encoding="utf-8") == original
    leftovers = [
        p for p in target.parent.iterdir() if p.name != target.name and ".tmp" in p.name
    ]
    assert leftovers == [], f"tempfiles leaked: {leftovers}"


def test_write_pointer_flock_blocks_concurrent_writer(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import SessionPointer, write_pointer

    ws = _ws(tmp_path)

    ready = multiprocessing.Event()
    done = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_hold_pointer_lock, args=(ws.root, ready, done)
    )
    proc.start()
    try:
        assert ready.wait(timeout=5), "child never acquired lock"

        finished = threading.Event()

        def writer() -> None:
            write_pointer(ws, SessionPointer(last_session_id=str(uuid.uuid4())))
            finished.set()

        t = threading.Thread(target=writer, daemon=True)
        t.start()

        assert not finished.wait(timeout=0.5), "writer did not block on lock"

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

    from daily_driver.core.session_pointer import SessionPointer

    with pytest.raises(ValidationError):
        SessionPointer(bogus_field="nope")  # type: ignore[call-arg]


def test_read_pointer_raises_error_with_path_on_corrupt_yaml(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import (
        SessionPointerError,
        pointer_path,
        read_pointer,
    )

    ws = _ws(tmp_path)
    target = pointer_path(ws)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("last_session_id: x\n  bad: : :\n", encoding="utf-8")

    with pytest.raises(SessionPointerError) as exc:
        read_pointer(ws)
    assert str(target) in str(exc.value)


def test_read_pointer_raises_error_on_top_level_non_mapping(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import (
        SessionPointerError,
        pointer_path,
        read_pointer,
    )

    ws = _ws(tmp_path)
    target = pointer_path(ws)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- just a list\n- not a mapping\n", encoding="utf-8")

    with pytest.raises(SessionPointerError) as exc:
        read_pointer(ws)
    assert "mapping" in str(exc.value).lower()
    assert str(target) in str(exc.value)
