"""Tests for ``daily-driver gather {calendar,git}``."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from daily_driver.gathers.calendar import CalendarEvent
from daily_driver.gathers.git import GitCommit


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


def test_gather_git_falls_back_to_cwd_when_no_repo_and_no_search_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.gathers import git

    ws = _init_workspace(tmp_path)
    seen: list[Path] = []

    def fake_gather(repo, since, until):
        seen.append(repo)
        return []

    monkeypatch.setattr(git, "gather_commits", fake_gather)
    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(ws), "gather", "git"])

    capsys.readouterr()
    assert rc == 0
    assert seen == [Path.cwd()]


def test_gather_git_uses_search_paths_when_no_repo_arg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.gathers import git

    ws = _init_workspace(tmp_path)
    # Two repos under a common root; configure that root as a search path.
    repos_root = tmp_path / "repos"
    repo_a = repos_root / "a"
    repo_b = repos_root / "b"
    for r in (repo_a, repo_b):
        (r / ".git").mkdir(parents=True)

    config_path = ws / ".dd-config.yaml"
    text = config_path.read_text()
    text += f"\ngather:\n  git:\n    search_paths:\n      - {repos_root}\n"
    config_path.write_text(text)

    seen: list[Path] = []

    def fake_gather(repo, since, until):
        seen.append(repo)
        return [
            GitCommit(
                sha=f"sha{repo.name}",
                timestamp=datetime(2026, 4, 20, 14, 30),
                author="x",
                subject=f"in {repo.name}",
            )
        ]

    monkeypatch.setattr(git, "gather_commits", fake_gather)

    rc = app(["--workspace", str(ws), "gather", "git"])

    out = capsys.readouterr().out
    assert rc == 0
    assert {p.resolve() for p in seen} == {repo_a.resolve(), repo_b.resolve()}
    assert "sha-a" in out or "shaa" in out
    assert "sha-b" in out or "shab" in out


def test_gather_git_search_paths_no_repos_prints_placeholder(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()
    config_path = ws / ".dd-config.yaml"
    text = config_path.read_text()
    text += f"\ngather:\n  git:\n    search_paths:\n      - {empty_root}\n"
    config_path.write_text(text)

    rc = app(["--workspace", str(ws), "gather", "git"])
    err = capsys.readouterr().err

    assert rc == 0
    assert "no git repos discovered" in err


def test_gather_git_search_paths_no_repos_json_emits_envelope(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`gather git --json` with a configured-but-empty search path must still emit
    the JSON envelope (empty commits), not short-circuit to empty stdout."""
    import json

    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()
    config_path = ws / ".dd-config.yaml"
    text = config_path.read_text()
    text += f"\ngather:\n  git:\n    search_paths:\n      - {empty_root}\n"
    config_path.write_text(text)

    rc = app(["--workspace", str(ws), "gather", "git", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    assert payload["data"]["count"] == 0
    assert payload["data"]["commits"] == []


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
            "calendar",
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
