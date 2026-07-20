"""Shared launcher helpers for nested Claude Code sessions.

Each daily-driver subcommand that orchestrates a Claude session (day-start,
day-end, check-in, summary) resolves the workspace, verifies auth, and
hands control to the `claude` CLI with a generated slash command as
the opening prompt.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from datetime import date
from pathlib import Path

from daily_driver.core import clock, focus
from daily_driver.core.console import Console
from daily_driver.core.daily_state import DailyStateError
from daily_driver.core.logging import get_logger
from daily_driver.core.session_pointer import (
    SessionPointer,
    SessionPointerError,
    read_pointer,
    write_pointer,
)
from daily_driver.core.workspace import Workspace, WorkspaceError
from daily_driver.integrations import claude_cli, notify, terminal_launcher

_log = get_logger(__name__)


class SessionError(RuntimeError):
    """Wraps expected launcher failures that should print cleanly and exit 1."""


def add_launch_mode_arg(parser: argparse.ArgumentParser) -> None:
    """Add the scheduler-facing --launch flag to a session launcher parser."""
    parser.add_argument(
        "--launch",
        choices=("terminal", "notify"),
        default=None,
        help=(
            "Scheduler firing mode: 'terminal' opens the session in a new"
            " iTerm2/Terminal tab; 'notify' posts a clickable notification"
            " that starts it. Omit for a normal interactive run."
        ),
    )


def _self_argv(cmd_name: str, workspace: Workspace) -> list[str]:
    """Rebuild the argv that relaunches this command interactively."""
    dd_bin = sys.argv[0]
    if not Path(dd_bin).is_absolute():
        dd_bin = shutil.which("daily-driver") or "daily-driver"
    return [dd_bin, cmd_name, "--workspace", str(workspace.root)]


def handle_launch_mode(
    args: argparse.Namespace,
    workspace: Workspace,
    cmd_name: str,
    *,
    respect_focus: bool = False,
) -> int | None:
    """Divert a scheduler firing per its --launch mode.

    Returns an exit code when the firing was handled (terminal tab opened,
    notification posted, or focus-suppressed), or None for a normal
    interactive run. launchd has no TTY, so the scheduled plists always pass
    a mode; the relaunched command carries no --launch flag and runs the
    ordinary interactive path in the fresh tab.
    """
    mode = getattr(args, "launch", None)
    if mode is None:
        return None

    relaunch = _self_argv(cmd_name, workspace)
    if mode == "terminal":
        try:
            terminal_launcher.open_in_terminal(relaunch)
        except terminal_launcher.TerminalLaunchError as exc:
            # Most commonly a denied/never-granted Automation permission.
            # Fall back to a notification so the firing is never fully silent.
            _log.warning("terminal launch failed for %s: %s", cmd_name, exc)
            notify.desktop_notify(
                "Daily Driver",
                f"Could not open a terminal for {cmd_name} ({exc});"
                f" run `daily-driver {cmd_name}` manually",
            )
            return 1
        notify.desktop_notify(
            "Daily Driver", f"{cmd_name} session opened in a terminal tab"
        )
        return 0

    # mode == "notify"
    if respect_focus and focus.is_active(workspace):
        _log.info("focus mode active; suppressing scheduled %s", cmd_name)
        return 0
    click_cmd = terminal_launcher.shell_command(relaunch + ["--launch", "terminal"])
    # Message is useful with or without click support: terminal-notifier makes
    # it clickable; osascript shows the manual command the user can copy.
    notify.desktop_notify(
        "Daily Driver",
        f"Time for {cmd_name} — run: daily-driver {cmd_name}",
        execute=click_cmd,
    )
    return 0


def require_claude_available() -> None:
    if not claude_cli.available():
        raise SessionError(
            "claude CLI not found on PATH. Install: " "https://claude.ai/download"
        )


def default_session_name(command: str, override: str | None = None) -> str:
    if override:
        return override
    return f"{command}-{date.today().isoformat()}"


def resolve_interactive_model(
    workspace: Workspace, cli_model: str | None
) -> str | None:
    """Resolve which claude model an interactive launcher should use.

    Interactive launchers (day-start, day-end, check-in) are claude-only, so
    this resolves a *model* only — never a provider. A `--model` CLI flag wins;
    otherwise the `ai.interactive.model` config default applies. Deliberately
    does NOT fall back to the global `ai.model`: that field is provider-agnostic
    and may hold an ollama tag, which the claude CLI cannot use.
    """
    return cli_model or workspace.config.ai.interactive.model


def launch_headless(
    *,
    slash_command: str,
    workspace: Workspace,
    session_name: str,
    agent: str = "work-planner",
    model: str | None = None,
    timeout: int = 180,
) -> str:
    """Run `slash_command` headlessly and return claude's stdout."""
    return claude_cli.invoke(
        prompt=slash_command,
        agent=agent,
        session_name=session_name,
        headless=True,
        add_dirs=[workspace.root],
        model=model,
        timeout=timeout,
    )


def launch_fresh_and_record(
    *,
    workspace: Workspace,
    prompt: str | None,
    session_name: str,
    agent: str,
    model: str | None,
    session_id: str | None = None,
) -> int:
    """Mint a session UUID, record it as the workspace's most-recent session, spawn.

    The pointer is written BEFORE the spawn so a still-running session is
    reattachable mid-life (a crashed tab can be resumed while claude is up).
    `session_id` lets a caller (day-start) pre-mint the UUID so it can record
    the same id in its own per-day state; when omitted a fresh one is minted.
    """
    resolved_id = session_id or str(uuid.uuid4())
    write_pointer(
        workspace,
        SessionPointer(last_session_id=resolved_id, last_session_at=clock.now()),
    )
    return claude_cli.spawn_interactive(
        prompt=prompt,
        agent=agent,
        session_name=session_name,
        add_dirs=[workspace.root],
        model=model,
        session_id=resolved_id,
    )


def reattach_or_fresh(
    *,
    workspace: Workspace,
    prompt: str | None,
    session_name: str,
    agent: str,
    model: str | None,
) -> int:
    """Reattach to the most-recent workspace session, or start fresh if none exists.

    Reads the workspace session pointer and, when present, reattaches via
    `claude --resume <uuid>` (replaying `prompt` into the resumed conversation),
    returning claude's exit code. When the recorded session can no longer be
    resumed, claude reports it (e.g. "No conversation found with session ID")
    and exits non-zero — that code propagates to the caller rather than silently
    launching an untethered fresh session. When no session has been recorded
    yet, a fresh one is started and recorded.
    """
    pointer = read_pointer(workspace)
    resume_id = pointer.last_session_id if pointer else None
    if resume_id is None:
        return launch_fresh_and_record(
            workspace=workspace,
            prompt=prompt,
            session_name=session_name,
            agent=agent,
            model=model,
        )
    return claude_cli.spawn_interactive(
        prompt=prompt,
        agent=agent,
        session_name=session_name,
        add_dirs=[workspace.root],
        model=model,
        resume_session_id=resume_id,
    )


def handle_launch_exception(exc: BaseException) -> int:
    """Translate subprocess / launcher failures into CLI exit codes with a user-visible message."""
    if isinstance(exc, (SessionError, WorkspaceError)):
        Console.error(str(exc))
        return 1
    if isinstance(exc, claude_cli.ClaudeNotFoundError):
        Console.error(str(exc))
        return 1
    if isinstance(exc, (DailyStateError, SessionPointerError)):
        # Both raise with the on-disk path baked in; surface it cleanly so the
        # user can hand-edit / delete the offending YAML.
        Console.error(str(exc))
        return 1
    if isinstance(exc, claude_cli.ClaudeTimeoutError):
        Console.error(f"claude session timed out after {exc.timeout}s")
        return 1
    if isinstance(exc, claude_cli.ClaudeInvocationError):
        stderr = (exc.stderr or "").strip()
        msg = f"claude exited {exc.returncode}"
        if stderr:
            msg = f"{msg}: {stderr}"
        Console.error(msg)
        return exc.returncode or 1
    if isinstance(exc, ValueError):
        # Programming-error guard surfaces (e.g. session_id + resume_session_id
        # passed together). Exit 2 = usage error, distinct from runtime failure.
        Console.error(str(exc))
        return 2
    if isinstance(exc, OSError):
        # Disk full / permission denied during plan-stub or state write. Print
        # the system error verbatim — the message already includes the path.
        Console.error(str(exc))
        return 1
    raise
