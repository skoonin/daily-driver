"""Tests for ``daily-driver ensure-daily-dir``."""

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


def test_ensure_daily_dir_creates_dir_and_prints_plan_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    today = date.today()
    expected_dir = ws.resolve() / f"{today.year:04d}" / f"{today.month:02d}"
    expected_plan = expected_dir / f"{today.isoformat()}-plan.md"

    rc = app(["--workspace", str(ws), "ensure-daily-dir"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out) == expected_plan
    assert expected_dir.is_dir()


def test_ensure_daily_dir_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc1 = app(["--workspace", str(ws), "ensure-daily-dir"])
    capsys.readouterr()
    rc2 = app(["--workspace", str(ws), "ensure-daily-dir"])
    out = capsys.readouterr().out.strip()

    assert rc1 == 0
    assert rc2 == 0
    assert "-plan.md" in out


def test_ensure_daily_dir_with_explicit_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(
        [
            "--workspace",
            str(ws),
            "ensure-daily-dir",
            "--date",
            "2025-12-31",
        ]
    )

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert (ws / "2025" / "12").is_dir()
    assert Path(out) == ws.resolve() / "2025" / "12" / "2025-12-31-plan.md"


def test_ensure_daily_dir_invalid_date_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(
        [
            "--workspace",
            str(ws),
            "ensure-daily-dir",
            "--date",
            "nope",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid --date" in captured.err


def test_ensure_daily_dir_missing_workspace_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(tmp_path / "missing"), "ensure-daily-dir"])

    assert rc == 1
