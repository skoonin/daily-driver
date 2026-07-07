"""Tests for the read-only focus-lock view (core.focus.is_active)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from daily_driver.core import focus
from daily_driver.core.workspace import Workspace


def _workspace(tmp_path: Path) -> Workspace:
    ws = tmp_path / "ws"
    ws.mkdir()
    return Workspace.init(ws)


def _write_lock(workspace: Workspace, end_epoch: int) -> Path:
    lock = workspace.ephemeral_dir / "focus.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"end_epoch": end_epoch}), encoding="utf-8")
    return lock


def test_no_lock_is_inactive(tmp_path: Path) -> None:
    assert focus.is_active(_workspace(tmp_path)) is False


def test_unexpired_lock_is_active(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_lock(ws, int(time.time()) + 3600)
    assert focus.is_active(ws) is True


def test_expired_lock_is_inactive_and_left_in_place(tmp_path: Path) -> None:
    """Expiry cleanup belongs to the `focus` command; the read-only view must
    not unlink the lock from a launchd context."""
    ws = _workspace(tmp_path)
    lock = _write_lock(ws, int(time.time()) - 60)
    assert focus.is_active(ws) is False
    assert lock.exists()


def test_malformed_lock_fails_open(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    lock = ws.ephemeral_dir / "focus.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("not json", encoding="utf-8")
    assert focus.is_active(ws) is False
    lock.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert focus.is_active(ws) is False
