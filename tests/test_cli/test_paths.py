"""Tests for ``daily-driver paths``."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest


def _init_workspace(tmp_path: Path) -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    return ws


def test_paths_output_prints_output_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "paths", "output"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    # output_dir defaults to `.` -> resolves to the workspace root
    assert Path(out) == ws.resolve()


def test_paths_state_prints_state_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "paths", "state"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out.endswith(".daily-driver")


def test_paths_daily_plan_uses_today_when_no_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    today = date.today()

    rc = app(["--workspace", str(ws), "paths", "daily-plan"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    expected = (
        ws.resolve()
        / f"{today.year:04d}"
        / f"{today.month:02d}"
        / f"{today.isoformat()}-plan.md"
    )
    assert Path(out) == expected


def test_paths_daily_notes_with_explicit_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "paths", "daily-notes", "--date", "2026-01-15"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out) == ws.resolve() / "2026" / "01" / "2026-01-15-notes.md"


def test_paths_daily_state_with_explicit_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "paths", "daily-state", "--date", "2026-05-08"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert (
        Path(out)
        == ws.resolve() / ".daily-driver" / "state" / "daily" / "2026-05-08.yaml"
    )


def test_paths_tracker_prints_tracker_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "paths", "tracker"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out) == ws.resolve() / "tracker.yaml"


def test_paths_json_includes_tracker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json as _json

    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "paths", "tracker", "--json"])
    assert rc == 0
    payload = _json.loads(capsys.readouterr().out)["data"]
    assert "tracker" in payload
    assert payload["tracker"].endswith("/tracker.yaml")


def test_paths_json_includes_daily_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json as _json

    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(
        [
            "--workspace",
            str(ws),
            "paths",
            "daily-plan",
            "--date",
            "2026-05-08",
            "--json",
        ]
    )
    assert rc == 0
    payload = _json.loads(capsys.readouterr().out)["data"]
    assert "daily_state" in payload
    assert payload["daily_state"].endswith("/.daily-driver/state/daily/2026-05-08.yaml")


def test_paths_without_workspace_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(tmp_path / "missing"), "paths", "output"])

    assert rc == 1


def test_paths_rejects_unknown_kind(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    with pytest.raises(SystemExit) as exc:
        app(["--workspace", str(ws), "paths", "bogus"])

    assert exc.value.code == 2
