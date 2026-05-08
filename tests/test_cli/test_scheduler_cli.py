"""CLI-level tests for install-scheduler / uninstall-scheduler dispatch.

Mocks the launchctl layer so tests pass on non-darwin CI too. Platform
validation is covered by test_core/test_scheduler.py::TestPlatformGuard.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _init_workspace(tmp_path: Path) -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    return ws


def _patch_launchd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    from daily_driver.integrations import launchd as launchd_int

    monkeypatch.setattr(sys, "platform", "darwin")

    fake_agents = tmp_path / "LaunchAgents"
    fake_agents.mkdir()
    calls: dict[str, list] = {"load": [], "unload": []}

    monkeypatch.setattr(launchd_int, "launch_agents_dir", lambda: fake_agents)
    monkeypatch.setattr(launchd_int, "load", lambda label: calls["load"].append(label))
    monkeypatch.setattr(
        launchd_int, "unload", lambda label: calls["unload"].append(label)
    )
    return calls


def test_install_scheduler_exits_0_and_reports_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "install-scheduler"])
    out = capsys.readouterr()
    combined = out.out + out.err

    assert rc == 0
    assert "com.daily-driver.checkin" in combined
    assert "com.daily-driver.scrape-jobs" in combined


def test_uninstall_scheduler_reports_no_op_when_nothing_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "uninstall-scheduler"])
    out = capsys.readouterr()
    combined = out.out + out.err

    assert rc == 0
    assert "No launchd agents" in combined


def test_uninstall_scheduler_rejects_removed_keep_state_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--keep-state was removed in v0.1.x; passing it now is a parse error."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        app(["--workspace", str(ws), "uninstall-scheduler", "--keep-state"])
    assert exc_info.value.code == 2


def test_uninstall_scheduler_always_removes_state_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    app(["--workspace", str(ws), "install-scheduler"])
    rc = app(["--workspace", str(ws), "uninstall-scheduler"])

    assert rc == 0
    state_dir = ws / ".daily-driver" / "state" / "launchd"
    assert not state_dir.exists(), "state mirror must be removed unconditionally"


def test_install_scheduler_errors_on_non_darwin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")

    rc = app(["--workspace", str(ws), "install-scheduler"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "macOS-only" in captured.err
