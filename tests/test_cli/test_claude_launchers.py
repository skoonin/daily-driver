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
    ("command", "slash"),
    [
        ("day-start", "/day-start"),
        ("day-end", "/day-end"),
        ("check-in", "/check-in"),
    ],
)
def test_interactive_launcher_invokes_claude_with_slash_command(
    command: str,
    slash: str,
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
    assert captured["session_name"].startswith(f"{command}-")


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
