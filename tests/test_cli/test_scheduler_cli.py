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
    assert "com.daily-driver.jobs" not in combined


def test_install_scheduler_includes_jobs_when_job_search_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from tests.workspaces import enable_job_search

    ws = _init_workspace(tmp_path)
    enable_job_search(ws)
    _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "scheduler", "install"])
    out = capsys.readouterr()
    combined = out.out + out.err

    assert rc == 0
    assert "com.daily-driver.jobs" in combined
    assert "com.daily-driver.jobs-discover" in combined


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
    from tests.workspaces import enable_job_search

    ws = _init_workspace(tmp_path)
    enable_job_search(ws)
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
    from tests.workspaces import enable_job_search

    ws = _init_workspace(tmp_path)
    enable_job_search(ws)
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


def _installed_then_switched_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> Path:
    """A workspace whose jobs agents were installed before job search went off."""
    from daily_driver.cli.cli import app
    from tests.workspaces import disable_job_search, enable_job_search

    ws = _init_workspace(tmp_path)
    enable_job_search(ws)
    _patch_launchd(monkeypatch, tmp_path)
    app(["--workspace", str(ws), "scheduler", "install"])
    disable_job_search(ws)
    capsys.readouterr()
    return ws


def test_install_after_switching_job_search_off_reports_removed_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _installed_then_switched_off(tmp_path, monkeypatch, capsys)

    rc = app(["--workspace", str(ws), "scheduler", "install"])

    out = " ".join(capsys.readouterr().out.split())
    assert rc == 0
    assert "Removed launchd agents (job search is off)" in out
    assert "com.daily-driver.jobs-discover" in out


def test_failed_install_still_reports_agents_it_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The sweep runs before loading; a later load failure must not hide it."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import launchd as launchd_int

    ws = _installed_then_switched_off(tmp_path, monkeypatch, capsys)

    def failing_load(label: str) -> None:
        raise launchd_int.LaunchdLoadError(label, 5, "boom")

    monkeypatch.setattr(launchd_int, "load", failing_load)

    rc = app(["--workspace", str(ws), "scheduler", "install"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "Removed launchd agents (job search is off)" in " ".join(
        captured.out.split()
    )
    assert "launchctl load failed" in captured.err


def test_install_crash_after_sweep_still_reports_removed_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Any error after the sweep, not only SchedulerError, reports removals."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import launchd as launchd_int

    ws = _installed_then_switched_off(tmp_path, monkeypatch, capsys)

    def denied(label: str, content: str) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(launchd_int, "write_plist", denied)

    rc = app(["--workspace", str(ws), "scheduler", "install"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "Removed launchd agents (job search is off)" in " ".join(
        captured.out.split()
    )
    assert "PermissionError" in captured.err


def test_install_failing_before_sweep_claims_no_removals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import launchd as launchd_int

    ws = _installed_then_switched_off(tmp_path, monkeypatch, capsys)

    def not_macos() -> None:
        raise launchd_int.LaunchdUnavailableError("scheduler is macOS-only")

    monkeypatch.setattr(launchd_int, "require_macos", not_macos)

    rc = app(["--workspace", str(ws), "scheduler", "install"])

    assert rc == 1
    assert "Removed" not in capsys.readouterr().out


def test_named_install_does_not_claim_to_remove_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only a full install sweeps; naming a job leaves the jobs agents alone."""
    from daily_driver.cli.cli import app

    ws = _installed_then_switched_off(tmp_path, monkeypatch, capsys)

    rc = app(["--workspace", str(ws), "scheduler", "install", "checkin"])

    assert rc == 0
    assert "Removed" not in capsys.readouterr().out


def test_status_table_flags_agents_left_installed_after_job_search_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _installed_then_switched_off(tmp_path, monkeypatch, capsys)

    rc = app(["--workspace", str(ws), "scheduler", "status"])

    out = " ".join(capsys.readouterr().out.split())
    assert rc == 0
    assert "yes, job search off" in out
    assert "Run `daily-driver scheduler install` to remove" in out


def test_status_lists_agents_left_installed_after_job_search_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    from daily_driver.cli.cli import app

    ws = _installed_then_switched_off(tmp_path, monkeypatch, capsys)

    rc = app(["--workspace", str(ws), "scheduler", "status", "--json"])

    rows = {row["label"]: row for row in json.loads(capsys.readouterr().out)["data"]}
    assert rc == 0
    assert rows["com.daily-driver.jobs"]["installed"] is True
    assert rows["com.daily-driver.jobs"]["plugin_off"] is True
    assert rows["com.daily-driver.checkin"]["plugin_off"] is False


def test_install_named_job_of_switched_off_plugin_names_the_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)

    rc = app(["--workspace", str(ws), "scheduler", "install", "jobs"])

    err = " ".join(capsys.readouterr().err.split())
    assert rc == 1
    assert "jobs: job search is off in this workspace" in err


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


@pytest.mark.parametrize("command", ["install", "status"])
def test_no_scheduled_runs_says_where_to_set_a_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _patch_launchd(monkeypatch, tmp_path)
    cfg = ws / ".dd-config.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + "scheduler:\n  checkin:\n    times: []\n",
        encoding="utf-8",
    )

    rc = app(["--workspace", str(ws), "scheduler", command])

    out = " ".join(capsys.readouterr().out.split())
    assert rc == 0
    assert "No scheduler jobs configured." in out
    assert "Set a time under `schedule:` or `scheduler:`" in out
