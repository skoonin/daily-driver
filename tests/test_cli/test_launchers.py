"""Tests for the interactive launcher subcommands (day-start/end, check-in, resume).

The launchers hand the terminal to the provider routed for the `interactive`
task. Tests stub ``daily_driver.integrations.provider.spawn`` and record the
argv it receives, so no actual subprocess ever runs. Exact argv lists for the
built-in claude and opencode providers are pinned in ``test_interactive_launch.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest


def _init_workspace(tmp_path: Path, config: str = "") -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    if config:
        cfg_path = ws / ".dd-config.yaml"
        cfg_path.write_text(
            cfg_path.read_text(encoding="utf-8") + config, encoding="utf-8"
        )
    return ws


def _stub_provider(
    monkeypatch: pytest.MonkeyPatch,
    exit_code_for: Callable[[list[str]], int] = lambda argv: 0,
) -> list[list[str]]:
    """Make the provider launchable and record each spawned argv.

    ``exit_code_for`` picks the exit code the fake session returns for an argv.
    """
    from daily_driver.integrations import provider

    calls: list[list[str]] = []

    def fake_spawn(chosen: object, argv: list[str]) -> int:
        calls.append(argv)
        return exit_code_for(argv)

    monkeypatch.setattr(provider, "available", lambda chosen: True)
    monkeypatch.setattr(provider, "spawn", fake_spawn)
    return calls


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    return _stub_provider(monkeypatch)


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ---------------------------------------------------------------------------
# day-start / day-end / check-in (interactive)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "slash", "session_prefix"),
    [
        ("day-start", "/daily-driver:day-start", "day-cycle"),
        ("day-end", "/daily-driver:day-end", "day-end"),
        ("check-in", "/daily-driver:check-in", "check-in"),
    ],
)
def test_interactive_launcher_invokes_claude_with_slash_command(
    command: str,
    slash: str,
    session_prefix: str,
    tmp_path: Path,
    spawned: list[list[str]],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), command])

    assert rc == 0
    (argv,) = spawned
    assert argv[0] == "claude"
    assert argv[-3] == slash
    assert _flag_value(argv, "--agent") == "work-planner"
    assert argv[-2:] == ["--add-dir", str(ws)]
    assert _flag_value(argv, "-n").startswith(f"{session_prefix}-")


def test_launcher_slash_prefix_matches_install_namespace() -> None:
    """The launchers' ``/daily-driver:`` prefix is hardcoded but the namespace
    is *derived* from where ``generate`` installs the command files
    (``commands/<namespace>``). Nothing else couples the two, so pin them: if
    the install-path leaf ever changes, the launchers would silently invoke a
    command that no longer resolves.
    """
    from daily_driver.cli.commands import check_in, day_start
    from daily_driver.core.generate import _CORE_PACKAGE_DATA

    commands_dir = next(
        entry.dest
        for entry in _CORE_PACKAGE_DATA
        if entry.source_package == "daily_driver.resources.slash_commands"
    )
    parent, _, namespace = commands_dir.partition("/")
    assert parent == "commands"
    expected_prefix = f"/{namespace}:"

    assert day_start._SLASH_COMMAND.startswith(expected_prefix)
    assert check_in._SLASH_COMMAND.startswith(expected_prefix)


def test_day_start_overrides_agent_model_and_session(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

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
    (argv,) = spawned
    assert _flag_value(argv, "--agent") == "custom-agent"
    assert _flag_value(argv, "--model") == "opus"
    assert _flag_value(argv, "-n") == "my-session"


@pytest.mark.parametrize(
    "command",
    ["day-start", "day-end", "check-in"],
)
def test_interactive_launcher_uses_config_model_default(
    command: str, tmp_path: Path, spawned: list[list[str]]
) -> None:
    """The claude provider's configured model is passed when no --model flag is given."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path, "\nai:\n  providers:\n    claude:\n      model: sonnet\n"
    )

    rc = app(["--workspace", str(ws), command])

    assert rc == 0
    (argv,) = spawned
    assert _flag_value(argv, "--model") == "sonnet"


def test_interactive_cli_model_overrides_config_default(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """An explicit --model flag wins over the provider's configured model."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(
        tmp_path, "\nai:\n  providers:\n    claude:\n      model: sonnet\n"
    )

    rc = app(["--workspace", str(ws), "day-start", "--model", "opus"])

    assert rc == 0
    (argv,) = spawned
    assert argv.count("--model") == 1
    assert _flag_value(argv, "--model") == "opus"


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
    assert "error" in captured.err.lower()


def test_day_start_writes_plan_stub_and_records_session_id(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """F2: day-start mints UUID, writes plan stub + state YAML, then launches."""

    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import read_state
    from daily_driver.core.session_pointer import read_pointer
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    today = clock.today()
    rc = app(["--workspace", str(ws_root), "day-start"])

    assert rc == 0
    (argv,) = spawned

    # 1. plan stub on disk under <workspace>/YYYY/MM/YYYY-MM-DD-plan.md
    ws = Workspace.discover_or_fail(override=ws_root)
    plan_path = (
        ws.root
        / f"{today.year:04d}"
        / f"{today.month:02d}"
        / f"{today.isoformat()}-plan.md"
    )
    assert plan_path.exists()
    body = plan_path.read_text(encoding="utf-8")
    assert f"date: {today.isoformat()}" in body

    # 2. state YAML records last_day_start_at (per-day marker)
    state = read_state(ws, today)
    assert state is not None
    assert state.last_day_start_at is not None

    # 2b. workspace session pointer records the launched session id
    #     (the resume/check-in source), matching the --session-id launch arg
    pointer = read_pointer(ws)
    assert pointer is not None
    assert pointer.last_session_id is not None
    assert pointer.last_session_at is not None
    assert _flag_value(argv, "--session-id") == pointer.last_session_id

    # 3. session-name carries the day-cycle prefix + ISO date
    assert today.isoformat() in _flag_value(argv, "-n")


def test_day_end_records_session_pointer(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """day-end mints a --session-id and records the workspace session pointer."""
    from daily_driver.cli.cli import app
    from daily_driver.core.session_pointer import read_pointer
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws_root), "day-end"])
    assert rc == 0

    (argv,) = spawned
    ws = Workspace.discover_or_fail(override=ws_root)
    pointer = read_pointer(ws)
    assert pointer is not None
    assert pointer.last_session_id is not None
    # The recorded pointer matches the --session-id handed to claude.
    assert _flag_value(argv, "--session-id") == pointer.last_session_id
    assert "--resume" not in argv


def test_day_start_does_not_clobber_existing_plan(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """F2: re-running day-start mid-day must preserve user/claude edits."""
    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    today = clock.today()
    ws = Workspace.discover_or_fail(override=ws_root)
    plan_path = (
        ws.root
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


def test_day_start_writes_late_day_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawned: list[list[str]]
) -> None:
    """F4: day-start records late_day=True when run past schedule.day_start + 2h."""
    from datetime import datetime, timezone

    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import read_state
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path, "\nschedule:\n  day_start: '07:00'\n")

    fake_now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)  # 3h past 07:00
    monkeypatch.setattr(clock, "FROZEN_TIME", fake_now)
    try:
        rc = app(["--workspace", str(ws_root), "day-start"])
    finally:
        monkeypatch.setattr(clock, "FROZEN_TIME", None)
    assert rc == 0

    ws = Workspace.discover_or_fail(override=ws_root)
    state = read_state(ws, fake_now.date())
    assert state is not None
    assert state.late_day is True


def test_day_start_preserves_prior_check_in_in_state(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """F2: a re-run of day-start must not nuke last_check_in_at written earlier."""
    from datetime import datetime, timezone

    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import DailyState, read_state, write_state
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    today = clock.today()
    ws = Workspace.discover_or_fail(override=ws_root)
    earlier = datetime(2026, 5, 8, 7, 30, tzinfo=timezone.utc)
    write_state(
        ws,
        DailyState(date=today, last_check_in_at=earlier),
    )

    rc = app(["--workspace", str(ws_root), "day-start"])
    assert rc == 0

    after = read_state(ws, today)
    assert after is not None
    assert after.last_check_in_at == earlier
    # day-start merged its marker in without clobbering the prior check-in.
    assert after.last_day_start_at is not None


def test_day_start_surfaces_daily_state_error_cleanly(
    tmp_path: Path,
    spawned: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2: a corrupted state YAML must produce 'error: <path>: ...' not a traceback."""

    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import state_path
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    ws = Workspace.discover_or_fail(override=ws_root)
    target = state_path(ws, clock.today())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- not\n- a mapping\n", encoding="utf-8")

    rc = app(["--workspace", str(ws_root), "day-start"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "error" in err.lower()
    assert str(target) in err.replace("\n", "")
    # No raw traceback should have surfaced.
    assert "Traceback" not in err


def test_day_start_surfaces_oserror_from_plan_stub_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2: a disk error during plan-stub write becomes 'error: ...' (no traceback)."""
    from daily_driver.cli.cli import app
    from daily_driver.cli.commands import day_start as day_start_mod

    ws = _init_workspace(tmp_path)

    def boom(_path: Path, _day: object) -> None:
        raise PermissionError("simulated read-only filesystem")

    monkeypatch.setattr(day_start_mod, "_write_plan_stub_if_absent", boom)

    rc = app(["--workspace", str(ws), "day-start"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "error" in err.lower()
    assert "simulated read-only filesystem" in err
    assert "Traceback" not in err


def test_check_in_does_not_resume_by_default(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """F3: ai.interactive.resume_check_in defaults False; no --resume even with a pointer."""
    from daily_driver.cli.cli import app
    from daily_driver.core.session_pointer import SessionPointer, write_pointer
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    ws = Workspace.discover_or_fail(override=ws_root)
    write_pointer(ws, SessionPointer(last_session_id="some-uuid-from-morning"))

    rc = app(["--workspace", str(ws_root), "check-in"])
    assert rc == 0
    (argv,) = spawned
    assert "--resume" not in argv
    assert "some-uuid-from-morning" not in argv


def test_check_in_resumes_when_config_enabled(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """F3: with ai.interactive.resume_check_in=true and a pointer, --resume <uuid> is passed."""
    from daily_driver.cli.cli import app
    from daily_driver.core.session_pointer import SessionPointer, write_pointer
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(
        tmp_path, "\nai:\n  interactive:\n    resume_check_in: true\n"
    )

    ws = Workspace.discover_or_fail(override=ws_root)
    sid = "11111111-2222-3333-4444-555555555555"
    write_pointer(ws, SessionPointer(last_session_id=sid))

    rc = app(["--workspace", str(ws_root), "check-in"])
    assert rc == 0
    (argv,) = spawned
    assert _flag_value(argv, "--resume") == sid
    assert "--session-id" not in argv


def test_check_in_propagates_resume_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: an unresumable id — claude exits non-zero — propagates, no second spawn.

    claude reports the failure itself ("No conversation found with session ID")
    on the inherited terminal; check-in does not silently re-launch a fresh
    session, and it records no check-in for a failed run.
    """
    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import read_state
    from daily_driver.core.session_pointer import SessionPointer, write_pointer
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(
        tmp_path, "\nai:\n  interactive:\n    resume_check_in: true\n"
    )

    # A bad --resume id makes claude exit 1; the spawn call itself never raises.
    calls = _stub_provider(monkeypatch, lambda argv: 1 if "--resume" in argv else 0)

    ws = Workspace.discover_or_fail(override=ws_root)
    sid = "11111111-2222-3333-4444-555555555555"
    write_pointer(ws, SessionPointer(last_session_id=sid))

    rc = app(["--workspace", str(ws_root), "check-in"])
    assert rc == 1
    # Exactly one spawn (the resume attempt); no silent fresh re-launch.
    assert len(calls) == 1
    assert _flag_value(calls[0], "--resume") == sid

    # A failed run records no check-in.
    after = read_state(ws, clock.today())
    assert after is None


def test_check_in_records_last_check_in_at_on_success(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """F3: a successful check-in updates last_check_in_at, preserving prior fields."""
    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import DailyState, read_state, write_state
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    ws = Workspace.discover_or_fail(override=ws_root)
    sid = "abc"
    write_state(
        ws,
        DailyState(
            date=clock.today(),
            last_day_start_session_id=sid,
        ),
    )

    rc = app(["--workspace", str(ws_root), "check-in"])
    assert rc == 0

    after = read_state(ws, clock.today())
    assert after is not None
    assert after.last_check_in_at is not None
    assert after.last_day_start_session_id == sid


def test_check_in_skips_state_update_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: a failed claude session must not record last_check_in_at (avoid lying)."""
    from daily_driver.cli.cli import app
    from daily_driver.core import clock
    from daily_driver.core.daily_state import read_state
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)
    _stub_provider(monkeypatch, lambda argv: 7)

    rc = app(["--workspace", str(ws_root), "check-in"])
    assert rc == 7

    ws = Workspace.discover_or_fail(override=ws_root)
    after = read_state(ws, clock.today())
    # No prior state → still none after a failed run.
    assert after is None


def test_check_in_state_write_failure_does_not_mask_session_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F3: a state-write failure post-session must warn, not flip rc to 1."""
    from daily_driver.cli.cli import app
    from daily_driver.cli.commands import check_in as check_in_mod

    ws = _init_workspace(tmp_path)

    def boom(_workspace: object) -> None:
        raise PermissionError("simulated read-only state dir")

    monkeypatch.setattr(check_in_mod, "_record_check_in", boom)

    rc = app(["--workspace", str(ws), "check-in"])
    err = capsys.readouterr().err

    assert rc == 0
    assert "warning" in err.lower()
    assert "last_check_in_at" in err
    assert "simulated read-only" in err
    assert "Traceback" not in err


def test_check_in_no_resume_flag_overrides_config(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    """F3: --no-resume forces a fresh session even when config says resume."""
    from daily_driver.cli.cli import app
    from daily_driver.core.session_pointer import SessionPointer, write_pointer
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(
        tmp_path, "\nai:\n  interactive:\n    resume_check_in: true\n"
    )

    ws = Workspace.discover_or_fail(override=ws_root)
    write_pointer(ws, SessionPointer(last_session_id="abc"))

    rc = app(["--workspace", str(ws_root), "check-in", "--no-resume"])
    assert rc == 0
    (argv,) = spawned
    assert "--resume" not in argv
    assert _flag_value(argv, "--session-id") != "abc"


def test_interactive_launcher_propagates_claude_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _stub_provider(monkeypatch, lambda argv: 42)

    rc = app(["--workspace", str(ws), "check-in"])

    assert rc == 42


# ---------------------------------------------------------------------------
# --launch scheduler firing modes (terminal tab / clickable notification)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["day-start", "day-end", "check-in"])
def test_launch_terminal_opens_tab_and_never_spawns_session(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import notify, provider, terminal_launcher

    ws = _init_workspace(tmp_path)
    opened: dict[str, list[str]] = {}
    monkeypatch.setattr(
        terminal_launcher,
        "open_in_terminal",
        lambda argv: opened.setdefault("argv", argv),
    )
    monkeypatch.setattr(notify, "desktop_notify", lambda *a, **k: True)

    def never_spawn(*args, **kwargs):  # pragma: no cover - failure path only
        raise AssertionError("a session must not spawn on a --launch firing")

    monkeypatch.setattr(provider, "spawn", never_spawn)

    rc = app(["--workspace", str(ws), command, "--launch", "terminal"])

    assert rc == 0
    argv = opened["argv"]
    assert argv[1] == command
    assert argv[argv.index("--workspace") + 1] == str(ws)
    # The relaunched command carries no --launch flag: it runs the ordinary
    # interactive path inside the fresh tab.
    assert "--launch" not in argv


def test_launch_terminal_day_start_defers_plan_stub_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The diverted firing must not write the plan stub or daily state --
    the relaunched interactive run performs those itself."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import notify, terminal_launcher

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(terminal_launcher, "open_in_terminal", lambda argv: None)
    monkeypatch.setattr(notify, "desktop_notify", lambda *a, **k: True)

    rc = app(["--workspace", str(ws), "day-start", "--launch", "terminal"])

    assert rc == 0
    assert not list(ws.glob("**/*-plan.md"))


def test_launch_terminal_failure_falls_back_to_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import notify, terminal_launcher

    ws = _init_workspace(tmp_path)

    def denied(argv):
        raise terminal_launcher.TerminalLaunchError("Not authorized (-1743)")

    notified: list[str] = []
    monkeypatch.setattr(terminal_launcher, "open_in_terminal", denied)
    monkeypatch.setattr(
        notify, "desktop_notify", lambda title, message, **kw: notified.append(message)
    )

    rc = app(["--workspace", str(ws), "day-end", "--launch", "terminal"])

    assert rc == 1
    assert any("day-end" in m for m in notified)


def test_launch_notify_posts_clickable_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import notify, provider

    ws = _init_workspace(tmp_path)
    captured: dict[str, object] = {}

    def fake_notify(title, message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)
        return True

    monkeypatch.setattr(notify, "desktop_notify", fake_notify)
    monkeypatch.setattr(
        provider,
        "spawn",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )

    rc = app(["--workspace", str(ws), "check-in", "--launch", "notify"])

    assert rc == 0
    # Clicking the notification relaunches check-in in terminal mode.
    execute = captured["execute"]
    assert "check-in" in execute
    assert "--launch terminal" in execute
    assert str(ws) in execute


def test_launch_notify_message_includes_manual_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notification message must include the manual run command so it's useful
    even when terminal-notifier is absent and the click action cannot fire."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import notify

    ws = _init_workspace(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_notify(title, message, **kwargs):
        calls.append({"message": message, **kwargs})
        return False  # osascript fallback: click action not delivered

    monkeypatch.setattr(notify, "desktop_notify", fake_notify)

    rc = app(["--workspace", str(ws), "check-in", "--launch", "notify"])

    assert rc == 0
    assert len(calls) == 1
    assert "daily-driver check-in" in str(calls[0]["message"])


def test_launch_notify_suppressed_by_focus_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import time

    from daily_driver.cli.cli import app
    from daily_driver.core.workspace import Workspace
    from daily_driver.integrations import notify

    ws_root = _init_workspace(tmp_path)
    ws = Workspace.discover_or_fail(override=ws_root)
    lock = ws.ephemeral_dir / "focus.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"end_epoch": int(time.time()) + 3600}), encoding="utf-8"
    )

    def must_not_notify(*args, **kwargs):  # pragma: no cover - failure path only
        raise AssertionError("focus mode must suppress the scheduled check-in")

    monkeypatch.setattr(notify, "desktop_notify", must_not_notify)

    rc = app(["--workspace", str(ws_root), "check-in", "--launch", "notify"])

    assert rc == 0


def test_launch_notify_focus_does_not_gate_day_bookends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Focus suppression is a check-in concept; a scheduled day-end firing
    still opens its tab while focus is on."""
    import json
    import time

    from daily_driver.cli.cli import app
    from daily_driver.core.workspace import Workspace
    from daily_driver.integrations import notify, terminal_launcher

    ws_root = _init_workspace(tmp_path)
    ws = Workspace.discover_or_fail(override=ws_root)
    lock = ws.ephemeral_dir / "focus.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"end_epoch": int(time.time()) + 3600}), encoding="utf-8"
    )

    opened: list[list[str]] = []
    monkeypatch.setattr(terminal_launcher, "open_in_terminal", opened.append)
    monkeypatch.setattr(notify, "desktop_notify", lambda *a, **k: True)

    rc = app(["--workspace", str(ws_root), "day-end", "--launch", "terminal"])

    assert rc == 0
    assert opened


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_resume_errors_cleanly_when_no_pointer(
    tmp_path: Path,
    spawned: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """resume with no recorded session errors, never opens an untethered session."""
    from daily_driver.cli.cli import app

    ws_root = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws_root), "resume"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "no prior session to resume" in err
    assert spawned == [], "resume must not spawn a session when none is recorded"


def test_resume_reattaches_to_pointer(tmp_path: Path, spawned: list[list[str]]) -> None:
    """resume passes --resume <uuid> (from the pointer) and no opening prompt."""
    from daily_driver.cli.cli import app
    from daily_driver.core.session_pointer import SessionPointer, write_pointer
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    ws = Workspace.discover_or_fail(override=ws_root)
    sid = "11111111-2222-3333-4444-555555555555"
    write_pointer(ws, SessionPointer(last_session_id=sid))

    rc = app(["--workspace", str(ws_root), "resume"])
    assert rc == 0
    (argv,) = spawned
    assert _flag_value(argv, "--resume") == sid
    # Reattach drops the user back into the conversation: no slash prompt replayed.
    assert not any(arg.startswith("/daily-driver:") for arg in argv)
    assert "--session-id" not in argv
    assert argv[-2:] == ["--add-dir", str(ws_root)]


def test_resume_propagates_reattach_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale/unresumable id — claude exits non-zero — propagates, no fresh spawn.

    claude itself reports "No conversation found with session ID" on the
    terminal and exits 1; resume returns that code rather than silently opening
    a bare untethered session.
    """
    from daily_driver.cli.cli import app
    from daily_driver.core.session_pointer import SessionPointer, write_pointer
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    # A bad --resume id makes claude exit 1; the spawn call itself never raises.
    calls = _stub_provider(monkeypatch, lambda argv: 1 if "--resume" in argv else 0)

    ws = Workspace.discover_or_fail(override=ws_root)
    sid = "11111111-2222-3333-4444-555555555555"
    write_pointer(ws, SessionPointer(last_session_id=sid))

    rc = app(["--workspace", str(ws_root), "resume"])

    assert rc == 1
    # Exactly one spawn (the resume attempt); no silent fresh re-launch.
    assert len(calls) == 1
    assert _flag_value(calls[0], "--resume") == sid


def test_resume_reports_corrupt_pointer_cleanly(
    tmp_path: Path,
    spawned: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A corrupt session.yaml yields a clean error with the path, not a traceback."""
    from daily_driver.cli.cli import app
    from daily_driver.core.session_pointer import pointer_path
    from daily_driver.core.workspace import Workspace

    ws_root = _init_workspace(tmp_path)

    ws = Workspace.discover_or_fail(override=ws_root)
    target = pointer_path(ws)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("last_session_id: x\n  bad: : :\n", encoding="utf-8")

    rc = app(["--workspace", str(ws_root), "resume"])
    err = capsys.readouterr().err

    assert rc == 1
    assert str(target) in err
    assert "Traceback" not in err
    assert spawned == []


def test_resume_rejects_launch_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resume is manual recovery: it does not accept the scheduler --launch mode."""
    from daily_driver.cli.cli import app

    ws_root = _init_workspace(tmp_path)

    with pytest.raises(SystemExit) as exc:
        app(["--workspace", str(ws_root), "resume", "--launch", "terminal"])
    assert exc.value.code == 2
