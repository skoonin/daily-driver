"""Tests for ``daily-driver read {context,voice-profile,plan}``."""

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


def test_read_context_prints_file_contents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    (ws / "context.md").write_text("Shawn works remotely from Vancouver.\n")

    rc = app(["--workspace", str(ws), "read", "context"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Shawn works remotely from Vancouver." in out


def test_read_context_missing_exits_1_with_helpful_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    (ws / "context.md").unlink(missing_ok=True)

    rc = app(["--workspace", str(ws), "read", "context"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "context.md not found" in captured.err


def test_read_voice_profile_missing_prints_marker_exits_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty-state contract: exit 0, stdout marker (matches `gather`).

    Skills running `daily-driver read voice-profile` in bash chains must
    not abort the chain when the profile doesn't exist yet.
    """
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "read", "voice-profile"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "no voice profile found" in captured.out


def test_read_voice_profile_empty_prints_marker_exits_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    (ws / "voice-profile.md").write_text("\n   \n")

    rc = app(["--workspace", str(ws), "read", "voice-profile"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "no voice profile found" in captured.out
    assert "empty" in captured.out


def test_read_voice_profile_prints_when_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    (ws / "voice-profile.md").write_text("# Voice profile\n\nTerse.\n")

    rc = app(["--workspace", str(ws), "read", "voice-profile"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Terse." in out


def test_read_plan_missing_prints_marker_exits_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "read", "plan"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "no plan found" in captured.out


def test_read_plan_prints_plan_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    day = date(2026, 3, 10)
    plan_dir = ws / "2026" / "03"
    plan_dir.mkdir(parents=True)
    plan_body = "---\ndate: 2026-03-10\nplan_items: []\n---\n\n# Plan body\n"
    (plan_dir / "2026-03-10-plan.md").write_text(plan_body)

    rc = app(["--workspace", str(ws), "read", "plan", "--date", day.isoformat()])

    out = capsys.readouterr().out
    assert rc == 0
    assert "# Plan body" in out
    assert "plan_items" in out


def test_read_plan_frontmatter_extracts_yaml_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    plan_dir = ws / "2026" / "03"
    plan_dir.mkdir(parents=True)
    body = "---\ndate: 2026-03-10\nplan_items:\n  - text: test\n---\n\n# body\n"
    (plan_dir / "2026-03-10-plan.md").write_text(body)

    rc = app(
        [
            "--workspace",
            str(ws),
            "read",
            "plan",
            "--date",
            "2026-03-10",
            "--frontmatter",
        ]
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "date: 2026-03-10" in out
    assert "# body" not in out


def test_read_plan_invalid_date_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "read", "plan", "--date", "not-a-date"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid --date" in captured.err


def test_read_bare_prints_usage_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "read"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "usage: daily-driver read" in captured.err
