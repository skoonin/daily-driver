"""CLI-level tests for `scheduler {install,uninstall,status}` dispatch.

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

    rc = app(["--workspace", str(ws), "scheduler", "install"])
    out = capsys.readouterr()
    combined = out.out + out.err

    assert rc == 0
    assert "com.daily-driver.checkin" in combined
    assert "com.daily-driver.jobs" in combined


def test_install_scheduler_selective_installs_only_named_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    calls = _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "scheduler", "install", "checkin"])
    out = capsys.readouterr()
    combined = out.out + out.err

    assert rc == 0
    assert calls["load"] == ["com.daily-driver.checkin"]
    assert "com.daily-driver.jobs" not in combined


def test_install_scheduler_unknown_job_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "scheduler", "install", "bogus"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "unknown scheduler job" in captured.err


def test_uninstall_scheduler_selective_removes_only_named_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    app(["--workspace", str(ws), "scheduler", "install"])
    rc = app(["--workspace", str(ws), "scheduler", "uninstall", "checkin"])
    out = capsys.readouterr()
    combined = out.out + out.err

    assert rc == 0
    assert "com.daily-driver.checkin" in combined
    state_dir = ws / ".daily-driver" / "state" / "launchd"
    # Only the named job's mirror is removed; the untouched one and dir survive.
    assert not (state_dir / "com.daily-driver.checkin.plist").exists()
    assert (state_dir / "com.daily-driver.jobs.plist").exists()


def test_uninstall_scheduler_reports_no_op_when_nothing_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "scheduler", "uninstall"])
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
        app(["--workspace", str(ws), "scheduler", "uninstall", "--keep-state"])
    assert exc_info.value.code == 2


def test_uninstall_scheduler_always_removes_state_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    app(["--workspace", str(ws), "scheduler", "install"])
    rc = app(["--workspace", str(ws), "scheduler", "uninstall"])

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

    rc = app(["--workspace", str(ws), "scheduler", "install"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "macOS-only" in captured.err


def test_scheduler_status_lists_configured_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`scheduler status` prints configured job labels and an installed marker."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "scheduler", "status"])
    out = capsys.readouterr()
    combined = out.out + out.err

    assert rc == 0
    assert "com.daily-driver.checkin" in combined
    assert "com.daily-driver.jobs" in combined


def test_scheduler_status_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`scheduler status --json` returns a structured payload."""
    import json

    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "scheduler", "status", "--json"])
    out = capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.out)
    assert payload["schema"] == 1
    labels = {row["label"] for row in payload["data"]}
    assert "com.daily-driver.checkin" in labels


def test_scheduler_bare_action_prints_usage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`scheduler` with no action prints usage and returns 2."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "scheduler"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "scheduler" in captured.err
