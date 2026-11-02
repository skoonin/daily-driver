"""Interactive launchers run through the provider routed for `interactive`.

The expected claude argv lists are written out literally so the built-in
claude provider's templates are held to the exact command Claude Code gets.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from daily_driver.core.clock import today


def _init_workspace(tmp_path: Path, config: str = "") -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    if config:
        cfg = ws / ".dd-config.yaml"
        cfg.write_text(cfg.read_text() + config, encoding="utf-8")
    return ws


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    from daily_driver.integrations import provider

    calls: list[list[str]] = []

    def fake_spawn(chosen: Any, argv: list[str]) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr(provider, "spawn", fake_spawn)
    monkeypatch.setattr(provider, "available", lambda chosen: True)
    return calls


def _pointer(ws: Path) -> Any:
    from daily_driver.core.session_pointer import read_pointer
    from daily_driver.core.workspace import Workspace

    return read_pointer(Workspace.discover_or_fail(override=ws))


def _session_id(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_day_start_fresh_argv_matches_the_claude_baseline(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "day-start"])

    assert rc == 0
    (argv,) = spawned
    session_id = _session_id(argv, "--session-id")
    uuid.UUID(session_id)
    assert argv == [
        "claude",
        "--agent",
        "work-planner",
        "-n",
        f"day-cycle-{today().isoformat()}",
        "--session-id",
        session_id,
        "/daily-driver:day-start",
        "--add-dir",
        str(ws),
    ]
    pointer = _pointer(ws)
    assert pointer.last_session_id == session_id
    assert pointer.provider == "claude"


def test_day_end_with_model_and_name_matches_the_claude_baseline(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(
        [
            "--workspace",
            str(ws),
            "day-end",
            "--agent",
            "custom-agent",
            "--model",
            "opus",
            "--session-name",
            "my-session",
        ]
    )

    assert rc == 0
    (argv,) = spawned
    assert argv == [
        "claude",
        "--agent",
        "custom-agent",
        "-n",
        "my-session",
        "--session-id",
        _session_id(argv, "--session-id"),
        "--model",
        "opus",
        "/daily-driver:day-end",
        "--add-dir",
        str(ws),
    ]


def test_resume_argv_matches_the_claude_baseline(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    assert app(["--workspace", str(ws), "day-start"]) == 0
    session_id = _session_id(spawned[0], "--session-id")

    rc = app(["--workspace", str(ws), "resume"])

    assert rc == 0
    assert spawned[1] == [
        "claude",
        "--agent",
        "work-planner",
        "-n",
        f"resume-{today().isoformat()}",
        "--resume",
        session_id,
        "--add-dir",
        str(ws),
    ]


def test_check_in_resume_replays_the_command_like_the_claude_baseline(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, "\nai:\n  interactive:\n    resume_check_in: true\n")
    assert app(["--workspace", str(ws), "day-start"]) == 0
    session_id = _session_id(spawned[0], "--session-id")

    rc = app(["--workspace", str(ws), "check-in"])

    assert rc == 0
    assert spawned[1] == [
        "claude",
        "--agent",
        "work-planner",
        "-n",
        f"check-in-{today().isoformat()}",
        "--resume",
        session_id,
        "/daily-driver:check-in",
        "--add-dir",
        str(ws),
    ]


def test_provider_model_applies_when_no_flag_is_given(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path, "\nai:\n  providers:\n    claude:\n      model: haiku\n"
    )

    assert app(["--workspace", str(ws), "day-start"]) == 0

    argv = spawned[0]
    assert argv[argv.index("--model") + 1] == "haiku"


def test_opencode_runs_interactive_with_its_own_session_ids(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path, "\nai:\n  providers:\n    opencode:\n      tasks: [interactive]\n"
    )

    assert app(["--workspace", str(ws), "day-start"]) == 0

    argv = spawned[0]
    assert argv[:3] == ["opencode", str(ws), "-s"]
    assert argv[3].startswith("ses_")
    pointer = _pointer(ws)
    assert pointer.provider == "opencode"
    assert pointer.last_session_id == argv[3]


def test_resume_refuses_a_session_from_another_provider(
    tmp_path: Path, spawned: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    assert app(["--workspace", str(ws), "day-start"]) == 0
    cfg = ws / ".dd-config.yaml"
    cfg.write_text(
        cfg.read_text()
        + "\nai:\n  providers:\n    opencode:\n      tasks: [interactive]\n",
        encoding="utf-8",
    )

    rc = app(["--workspace", str(ws), "resume"])

    assert rc == 1
    assert len(spawned) == 1
    err = capsys.readouterr().err
    assert "started with claude" in err
    assert "opencode" in err


def test_resume_needs_a_resume_template(
    tmp_path: Path, spawned: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path,
        '\nai:\n  providers:\n    mine:\n      session_id: "{uuid}"\n'
        '      run: [mytool, ["{command}"]]\n      tasks: [interactive]\n',
    )
    assert app(["--workspace", str(ws), "day-start"]) == 0

    rc = app(["--workspace", str(ws), "resume"])

    assert rc == 1
    assert "mine has no resume template" in capsys.readouterr().err


def test_provider_without_session_ids_records_no_pointer(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path,
        '\nai:\n  providers:\n    mine:\n      run: [mytool, ["{command}"]]\n'
        "      tasks: [interactive]\n",
    )

    assert app(["--workspace", str(ws), "day-start"]) == 0

    assert spawned == [["mytool", "/daily-driver:day-start"]]
    assert _pointer(ws) is None


def test_provider_without_session_ids_clears_an_older_pointer(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    assert app(["--workspace", str(ws), "day-start"]) == 0
    assert _pointer(ws) is not None
    cfg = ws / ".dd-config.yaml"
    cfg.write_text(
        cfg.read_text()
        + '\nai:\n  providers:\n    mine:\n      run: [mytool, ["{command}"]]\n'
        + "      tasks: [interactive]\n",
        encoding="utf-8",
    )

    assert app(["--workspace", str(ws), "day-end"]) == 0

    assert spawned[1] == ["mytool", "/daily-driver:day-end"]
    assert _pointer(ws) is None


def test_missing_program_names_it_and_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import provider

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(provider, "available", lambda chosen: False)

    rc = app(["--workspace", str(ws), "day-start"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "claude not found on PATH" in err
    assert "Install it, or give another provider the interactive task" in err


def test_old_pointer_without_provider_reads_as_claude(tmp_path: Path) -> None:
    from daily_driver.core.session_pointer import SessionPointer

    pointer = SessionPointer.model_validate({"last_session_id": "abc"})
    assert pointer.provider == "claude"


def test_opencode_gets_the_prompt_file(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path, "\nai:\n  providers:\n    opencode:\n      tasks: [interactive]\n"
    )

    assert app(["--workspace", str(ws), "check-in"]) == 0

    prompt_file = ws / ".daily-driver" / "state" / "prompts" / "check-in.md"
    argv = spawned[0]
    assert argv[-2:] == ["--prompt", f"Read {prompt_file} and follow its instructions."]
    assert "Mid-day check-in session" in prompt_file.read_text(encoding="utf-8")


def test_opencode_plain_resume_sends_no_prompt(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path, "\nai:\n  providers:\n    opencode:\n      tasks: [interactive]\n"
    )
    assert app(["--workspace", str(ws), "day-start"]) == 0
    session_id = spawned[0][3]

    assert app(["--workspace", str(ws), "resume"]) == 0

    assert spawned[1] == ["opencode", str(ws), "-s", session_id]


def test_claude_writes_no_prompt_file(tmp_path: Path, spawned: list[list[str]]) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    assert app(["--workspace", str(ws), "day-start"]) == 0

    assert not (ws / ".daily-driver" / "state" / "prompts").exists()


def test_check_in_starts_fresh_after_a_provider_switch(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, "\nai:\n  interactive:\n    resume_check_in: true\n")
    assert app(["--workspace", str(ws), "day-start"]) == 0
    cfg = ws / ".dd-config.yaml"
    cfg.write_text(
        cfg.read_text() + "  providers:\n    opencode:\n      tasks: [interactive]\n",
        encoding="utf-8",
    )

    assert app(["--workspace", str(ws), "check-in"]) == 0

    argv = spawned[1]
    assert argv[:3] == ["opencode", str(ws), "-s"]
    assert _pointer(ws).provider == "opencode"


def test_resume_on_a_provider_without_session_ids_says_why(
    tmp_path: Path, spawned: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path,
        "\nai:\n  providers:\n    mine:\n      run: [mytool]\n"
        "      tasks: [interactive]\n",
    )

    assert app(["--workspace", str(ws), "resume"]) == 1
    assert "cannot be resumed" in capsys.readouterr().err
