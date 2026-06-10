"""Tests for daily_driver.cli.commands.focus — focus mode subcommand."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import daily_driver.core.clock as clock_mod
from daily_driver.cli.commands.focus import _parse_duration, run
from daily_driver.core.workspace import Workspace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace.init(tmp_path)


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "verbose": False,
        "quiet": False,
        "no_color": False,
        "workspace": None,
        "focus_action": None,
        "func": run,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _on_args(workspace_path: Path, **kwargs: Any) -> argparse.Namespace:
    from daily_driver.cli.commands.focus import _run_on

    return _args(
        func=_run_on,
        focus_action="on",
        workspace=str(workspace_path),
        duration=kwargs.get("duration", 30),
        reason=kwargs.get("reason", None),
    )


def _off_args(workspace_path: Path) -> argparse.Namespace:
    from daily_driver.cli.commands.focus import _run_off

    return _args(
        func=_run_off,
        focus_action="off",
        workspace=str(workspace_path),
    )


def _status_args(workspace_path: Path) -> argparse.Namespace:
    from daily_driver.cli.commands.focus import _run_status

    return _args(
        func=_run_status,
        focus_action="status",
        workspace=str(workspace_path),
    )


# ---------------------------------------------------------------------------
# 1. focus on creates lock file with correct structure
# ---------------------------------------------------------------------------


def test_focus_on_uses_config_default_duration(workspace: Workspace) -> None:
    """`focus on` without --for falls back to focus.default_duration in config."""
    # Workspace.init() seeds .dd-config.yaml with the default 25m via FocusConfig.
    frozen = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen
    try:
        args = _on_args(workspace.root, duration=None)
        result = run(args)
    finally:
        clock_mod.FROZEN_TIME = None
    assert result == 0
    lock = workspace.ephemeral_dir / "focus.lock"
    assert lock.exists()
    data = json.loads(lock.read_text())
    # 25 minutes default
    assert data["end_epoch"] == int(frozen.replace(minute=25).timestamp())


def test_focus_on_creates_lock_file(workspace: Workspace) -> None:
    frozen = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen
    try:
        args = _on_args(workspace.root, duration=30, reason="deep work")
        result = run(args)
    finally:
        clock_mod.FROZEN_TIME = None

    assert result == 0
    lock = workspace.ephemeral_dir / "focus.lock"
    assert lock.exists()
    data = json.loads(lock.read_text())
    assert data["reason"] == "deep work"
    assert data["end_epoch"] == int((frozen.replace(minute=30)).timestamp())
    assert "start_iso" in data
    assert "end_iso" in data


def test_focus_on_write_is_atomic(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    """L-3: the focus.lock payload write goes through a temp file + os.replace
    so a crash mid-write can never leave a truncated/partial JSON payload."""
    import daily_driver.cli.commands.focus as focus_mod

    replaced: list[tuple[str, str]] = []
    orig_replace = focus_mod.os.replace

    def tracking_replace(src: Any, dst: Any) -> None:
        replaced.append((str(src), str(dst)))
        orig_replace(src, dst)

    monkeypatch.setattr(focus_mod.os, "replace", tracking_replace)

    frozen = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen
    try:
        result = run(_on_args(workspace.root, duration=30, reason="atomic"))
    finally:
        clock_mod.FROZEN_TIME = None

    assert result == 0
    lock = workspace.ephemeral_dir / "focus.lock"
    assert lock.exists()
    # Payload landed via os.replace from a temp sibling, not a direct write.
    assert len(replaced) == 1
    src, dst = replaced[0]
    assert dst == str(lock)
    assert src != str(lock)
    # No leftover temp file beside the lock.
    leftovers = [p for p in workspace.ephemeral_dir.iterdir() if p.name != "focus.lock"]
    assert leftovers == []
    # Final payload is complete, valid JSON.
    data = json.loads(lock.read_text())
    assert data["reason"] == "atomic"


# ---------------------------------------------------------------------------
# 2. focus on with existing lock overwrites cleanly
# ---------------------------------------------------------------------------


def test_focus_on_overwrites_existing_lock(workspace: Workspace) -> None:
    frozen1 = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    frozen2 = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc)

    clock_mod.FROZEN_TIME = frozen1
    try:
        run(_on_args(workspace.root, duration=30, reason="first"))
    finally:
        clock_mod.FROZEN_TIME = None

    clock_mod.FROZEN_TIME = frozen2
    try:
        result = run(_on_args(workspace.root, duration=60, reason="second"))
    finally:
        clock_mod.FROZEN_TIME = None

    assert result == 0
    lock = workspace.ephemeral_dir / "focus.lock"
    data = json.loads(lock.read_text())
    assert data["reason"] == "second"
    expected_end = int(
        (frozen2 + __import__("datetime").timedelta(minutes=60)).timestamp()
    )
    assert data["end_epoch"] == expected_end


# ---------------------------------------------------------------------------
# 3. focus off deletes lock and prints expected message
# ---------------------------------------------------------------------------


def test_focus_off_deletes_lock(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    frozen = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen
    try:
        run(_on_args(workspace.root, duration=30))
    finally:
        clock_mod.FROZEN_TIME = None

    lock = workspace.ephemeral_dir / "focus.lock"
    assert lock.exists()

    result = run(_off_args(workspace.root))
    assert result == 0
    assert not lock.exists()
    captured = capsys.readouterr()
    assert "Focus mode disabled" in captured.out


# ---------------------------------------------------------------------------
# 4. focus off with no lock prints "not in focus mode" and exits 0
# ---------------------------------------------------------------------------


def test_focus_off_no_lock(workspace: Workspace, capsys: pytest.CaptureFixture) -> None:
    result = run(_off_args(workspace.root))
    assert result == 0
    captured = capsys.readouterr()
    assert "not in focus mode" in captured.out


# ---------------------------------------------------------------------------
# 5. focus status with no lock → "not in focus mode"
# ---------------------------------------------------------------------------


def test_focus_status_no_lock(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    result = run(_status_args(workspace.root))
    assert result == 0
    captured = capsys.readouterr()
    assert "not in focus mode" in captured.out


# ---------------------------------------------------------------------------
# 6. focus status with active lock → prints "Active" and remaining minutes
# ---------------------------------------------------------------------------


def test_focus_status_active(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    frozen_start = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen_start
    try:
        run(_on_args(workspace.root, duration=60, reason="heads down"))
    finally:
        clock_mod.FROZEN_TIME = None

    # Status check 10 minutes later
    frozen_check = datetime(2026, 4, 20, 9, 10, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen_check
    try:
        result = run(_status_args(workspace.root))
    finally:
        clock_mod.FROZEN_TIME = None

    assert result == 0
    captured = capsys.readouterr()
    assert "Active" in captured.out
    assert "50m remaining" in captured.out
    assert "Reason: heads down" in captured.out


# ---------------------------------------------------------------------------
# 7. focus status with expired lock → deletes and prints "expired"
# ---------------------------------------------------------------------------


def test_focus_status_expired(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    frozen_start = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen_start
    try:
        run(_on_args(workspace.root, duration=30))
    finally:
        clock_mod.FROZEN_TIME = None

    lock = workspace.ephemeral_dir / "focus.lock"
    assert lock.exists()

    # Check status 1 hour later (lock expired)
    frozen_after = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen_after
    try:
        result = run(_status_args(workspace.root))
    finally:
        clock_mod.FROZEN_TIME = None

    assert result == 0
    assert not lock.exists()
    captured = capsys.readouterr()
    assert "expired" in captured.out.lower()


def test_focus_status_expired_unlink_is_lock_guarded(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    """Regression: expired-lock cleanup must serialize via file_lock so a
    concurrent launchd check-in tick cannot race and observe a half-deleted
    state (or error on an already-removed file)."""
    frozen_start = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen_start
    try:
        run(_on_args(workspace.root, duration=30))
    finally:
        clock_mod.FROZEN_TIME = None

    import daily_driver.cli.commands.focus as focus_mod

    called: list[dict[str, Any]] = []
    orig_file_lock = focus_mod.file_lock

    def tracking_lock(*args: Any, **kwargs: Any) -> Any:
        called.append({"args": args, "kwargs": kwargs})
        return orig_file_lock(*args, **kwargs)

    monkeypatch.setattr(focus_mod, "file_lock", tracking_lock)

    frozen_after = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen_after
    try:
        result = run(_status_args(workspace.root))
    finally:
        clock_mod.FROZEN_TIME = None

    assert result == 0
    assert not (workspace.ephemeral_dir / "focus.lock").exists()
    assert len(called) == 1, "expired-lock unlink must be wrapped in file_lock"
    assert called[0]["kwargs"].get("shared") is False


# ---------------------------------------------------------------------------
# 8. Bad duration via app exits 2
# ---------------------------------------------------------------------------


def test_bad_duration_exits_2() -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc_info:
        app(["focus", "on", "--for", "bogus"])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 9. Duration parsing: 30m, 2h, 1h30m, 90 all parse correctly
# ---------------------------------------------------------------------------


def test_duration_parsing_30m() -> None:
    assert _parse_duration("30m") == 30


def test_duration_parsing_2h() -> None:
    assert _parse_duration("2h") == 120


def test_duration_parsing_1h30m() -> None:
    assert _parse_duration("1h30m") == 90


def test_duration_parsing_bare_int() -> None:
    assert _parse_duration("90") == 90


def test_duration_parsing_invalid_raises() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_duration("bogus")


def test_duration_parsing_zero_hours_only() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_duration("0h")
