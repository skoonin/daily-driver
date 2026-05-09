"""Tests for nested-claude launcher subcommands (day-start/end, check-in).

These commands delegate to `claude` via ``daily_driver.integrations.claude_cli``.
Tests stub out ``claude_cli.spawn_interactive`` and ``claude_cli.invoke`` so no
actual subprocess ever runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _init_workspace(tmp_path: Path) -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    return ws


# ---------------------------------------------------------------------------
# day-start / day-end / check-in (interactive)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "slash", "session_prefix"),
    [
        ("day-start", "/day-start", "day-cycle"),
        ("day-end", "/day-end", "day-end"),
        ("check-in", "/check-in", "check-in"),
    ],
)
def test_interactive_launcher_invokes_claude_with_slash_command(
    command: str,
    slash: str,
    session_prefix: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)

    captured: dict[str, object] = {}

    def fake_spawn(prompt=None, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(claude_cli, "spawn_interactive", fake_spawn)

    rc = app(["--workspace", str(ws), command])

    assert rc == 0
    assert captured["prompt"] == slash
    assert captured["agent"] == "work-planner"
    assert captured["add_dirs"] == [ws]
    assert captured["session_name"].startswith(f"{session_prefix}-")


def test_day_start_overrides_agent_model_and_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)

    captured: dict[str, object] = {}

    def fake_spawn(prompt=None, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(claude_cli, "spawn_interactive", fake_spawn)

    rc = app(
        [
            "--workspace",
            str(ws),
            "day-start",
            "--agent",
            "custom-agent",
            "--model",
            "opus",
            "--session-name",
            "my-session",
        ]
    )

    assert rc == 0
    assert captured["agent"] == "custom-agent"
    assert captured["model"] == "opus"
    assert captured["session_name"] == "my-session"


def test_interactive_launcher_missing_claude_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: False)

    rc = app(["--workspace", str(ws), "day-start"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "claude CLI not found" in captured.err


def test_interactive_launcher_missing_workspace_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(tmp_path / "missing"), "day-start"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "error:" in captured.err


def test_day_start_writes_plan_stub_and_records_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2: day-start mints UUID, writes plan stub + state YAML, then launches."""

    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import read_state
    from daily_driver.core.workspace import Workspace
    from daily_driver.integrations import claude_cli

    ws_root = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)

    captured: dict[str, object] = {}

    def fake_spawn(prompt=None, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(claude_cli, "spawn_interactive", fake_spawn)

    today = clock.today()
    rc = app(["--workspace", str(ws_root), "day-start"])

    assert rc == 0

    # 1. plan stub on disk under output_dir/YYYY/MM/YYYY-MM-DD-plan.md
    ws = Workspace.discover_or_fail(override=ws_root)
    plan_path = (
        ws.output_dir
        / f"{today.year:04d}"
        / f"{today.month:02d}"
        / f"{today.isoformat()}-plan.md"
    )
    assert plan_path.exists()
    body = plan_path.read_text(encoding="utf-8")
    assert f"date: {today.isoformat()}" in body

    # 2. state YAML records session_id + last_day_start_at, and matches the launch arg
    state = read_state(ws, today)
    assert state is not None
    assert state.last_day_start_session_id is not None
    assert state.last_day_start_at is not None
    assert captured.get("session_id") == state.last_day_start_session_id

    # 3. session-name carries the day-cycle prefix + ISO date
    assert isinstance(captured["session_name"], str)
    assert today.isoformat() in captured["session_name"]


def test_day_start_does_not_clobber_existing_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2: re-running day-start mid-day must preserve user/claude edits."""
    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.workspace import Workspace
    from daily_driver.integrations import claude_cli

    ws_root = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "spawn_interactive", lambda **kw: 0)

    today = clock.today()
    ws = Workspace.discover_or_fail(override=ws_root)
    plan_path = (
        ws.output_dir
        / f"{today.year:04d}"
        / f"{today.month:02d}"
        / f"{today.isoformat()}-plan.md"
    )
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    user_content = "---\ndate: " + today.isoformat() + "\n---\n\nHand-edited.\n"
    plan_path.write_text(user_content, encoding="utf-8")

    rc = app(["--workspace", str(ws_root), "day-start"])
    assert rc == 0
    assert plan_path.read_text(encoding="utf-8") == user_content


def test_day_start_preserves_prior_check_in_in_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2: a re-run of day-start must not nuke last_check_in_at written earlier."""
    from datetime import datetime, timezone

    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import DailyState, read_state, write_state
    from daily_driver.core.workspace import Workspace
    from daily_driver.integrations import claude_cli

    ws_root = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "spawn_interactive", lambda **kw: 0)

    today = clock.today()
    ws = Workspace.discover_or_fail(override=ws_root)
    earlier = datetime(2026, 5, 8, 7, 30, tzinfo=timezone.utc)
    write_state(
        ws,
        DailyState(date=today, last_check_in_at=earlier, plan_summary="prior"),
    )

    rc = app(["--workspace", str(ws_root), "day-start"])
    assert rc == 0

    after = read_state(ws, today)
    assert after is not None
    assert after.last_check_in_at == earlier
    assert after.plan_summary == "prior"
    assert after.last_day_start_session_id is not None


def test_day_start_surfaces_daily_state_error_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2: a corrupted state YAML must produce 'error: <path>: ...' not a traceback."""

    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import state_path
    from daily_driver.core.workspace import Workspace
    from daily_driver.integrations import claude_cli

    ws_root = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "spawn_interactive", lambda **kw: 0)

    ws = Workspace.discover_or_fail(override=ws_root)
    target = state_path(ws, clock.today())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- not\n- a mapping\n", encoding="utf-8")

    rc = app(["--workspace", str(ws_root), "day-start"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "error:" in err
    assert str(target) in err
    # No raw traceback should have surfaced.
    assert "Traceback" not in err


def test_day_start_surfaces_oserror_from_plan_stub_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2: a disk error during plan-stub write becomes 'error: ...' (no traceback)."""
    from daily_driver.cli.cli import app
    from daily_driver.cli.commands import day_start as day_start_mod
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "spawn_interactive", lambda **kw: 0)

    def boom(_path: Path, _day: object) -> None:
        raise PermissionError("simulated read-only filesystem")

    monkeypatch.setattr(day_start_mod, "_write_plan_stub_if_absent", boom)

    rc = app(["--workspace", str(ws), "day-start"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "error:" in err
    assert "simulated read-only filesystem" in err
    assert "Traceback" not in err


def test_interactive_launcher_propagates_claude_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "spawn_interactive", lambda **kw: 42)

    rc = app(["--workspace", str(ws), "check-in"])

    assert rc == 42
