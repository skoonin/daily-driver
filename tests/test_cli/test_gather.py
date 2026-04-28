"""Tests for ``daily-driver gather {calendar,git,sessions,notes}``."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from daily_driver.gathers.calendar import CalendarEvent
from daily_driver.gathers.git import GitCommit
from daily_driver.gathers.sessions import ClaudeSession


def _init_workspace(tmp_path: Path) -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    return ws


def test_gather_calendar_text_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.gathers import calendar

    ws = _init_workspace(tmp_path)
    event = CalendarEvent(
        title="Standup",
        start=datetime(2026, 4, 21, 9, 0),
        end=datetime(2026, 4, 21, 9, 15),
        location="Zoom",
    )
    monkeypatch.setattr(calendar, "gather_events", lambda since, until: [event])

    rc = app(["--workspace", str(ws), "gather", "calendar"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "Standup" in captured.out
    assert "09:00-09:15" in captured.out
    assert "Zoom" in captured.out


def test_gather_calendar_json_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.gathers import calendar

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(calendar, "gather_events", lambda since, until: [])

    rc = app(["--workspace", str(ws), "gather", "calendar", "--json"])

    captured = capsys.readouterr()
    assert rc == 0
    parsed = json.loads(captured.out)
    assert parsed["schema"] == 1
    assert parsed["data"]["events"] == []
    assert parsed["data"]["count"] == 0


def test_gather_calendar_empty_prints_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.gathers import calendar

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(calendar, "gather_events", lambda since, until: [])

    rc = app(["--workspace", str(ws), "gather", "calendar"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "no calendar events" in out


def test_gather_git_text_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.gathers import git

    ws = _init_workspace(tmp_path)
    commit = GitCommit(
        sha="abc1234",
        timestamp=datetime(2026, 4, 20, 14, 30),
        author="Shawn",
        subject="Add widget",
    )
    monkeypatch.setattr(git, "gather_commits", lambda repo, since, until: [commit])

    rc = app(
        [
            "--workspace",
            str(ws),
            "gather",
            "git",
            "--repo",
            str(tmp_path),
        ]
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "abc1234" in out
    assert "Add widget" in out


def test_gather_sessions_text_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.gathers import sessions

    ws = _init_workspace(tmp_path)
    session = ClaudeSession(
        session_id="a" * 32,
        started_at=datetime(2026, 4, 21, 10, 0),
        cwd=str(tmp_path),
        message_count=12,
    )
    monkeypatch.setattr(sessions, "gather_sessions", lambda since, until: [session])

    rc = app(["--workspace", str(ws), "gather", "sessions"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "msgs=12" in out
    assert "aaaaaaaa" in out


def test_gather_notes_lists_paths_in_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    (ws / "2026" / "04").mkdir(parents=True)
    note = ws / "2026" / "04" / "2026-04-20-notes.md"
    note.write_text("notes\n")

    rc = app(
        [
            "--workspace",
            str(ws),
            "gather",
            "notes",
            "--since",
            "2026-04-19",
            "--until",
            "2026-04-21",
        ]
    )

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert str(note) in out


def test_gather_notes_empty_prints_placeholder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "gather", "notes"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "no notes in window" in out


def test_gather_bare_prints_usage_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "gather"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "usage: daily-driver gather" in captured.err


def test_gather_invalid_date_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(
        [
            "--workspace",
            str(ws),
            "gather",
            "notes",
            "--since",
            "not-a-date",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid date" in captured.err


def test_gather_missing_workspace_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(
        [
            "--workspace",
            str(tmp_path / "missing"),
            "gather",
            "calendar",
        ]
    )

    assert rc == 1


def test_gather_notes_uses_workspace_output_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """notes honors output_dir resolution (not just workspace root)."""
    from daily_driver.cli.cli import app
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir()
    Workspace.init(ws)
    # Rewrite output_dir to a sibling directory
    config = ws / ".dd-config.yaml"
    config.write_text(
        config.read_text().replace("output_dir: .", "output_dir: ../output"),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    (output / "2026" / "04").mkdir(parents=True)
    note = output / "2026" / "04" / "2026-04-20-notes.md"
    note.write_text("x\n")

    rc = app(
        [
            "--workspace",
            str(ws),
            "gather",
            "notes",
            "--since",
            "2026-04-19",
            "--until",
            "2026-04-21",
        ]
    )

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert str(note.resolve()) in out


def test_gather_calendar_unused_dates_default_to_today(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When --since/--until omitted, defaults derive from clock.today()."""
    from daily_driver.cli.cli import app
    from daily_driver.gathers import calendar

    ws = _init_workspace(tmp_path)
    captured_args = {}

    def fake_gather(since, until):
        captured_args["since"] = since
        captured_args["until"] = until
        return []

    monkeypatch.setattr(calendar, "gather_events", fake_gather)

    rc = app(["--workspace", str(ws), "gather", "calendar"])

    assert rc == 0
    # Defaults to [today, today+1day)
    assert captured_args["since"].date() == date.today()
