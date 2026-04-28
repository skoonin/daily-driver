"""Shared launcher helpers for nested Claude Code sessions.

Each daily-driver subcommand that orchestrates a Claude session (day-start,
day-end, check-in, summary) resolves the workspace, verifies auth, and
hands control to the `claude` CLI with a materialized slash command as
the opening prompt.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from daily_driver.core.workspace import Workspace, WorkspaceError
from daily_driver.integrations import claude_cli


class SessionError(RuntimeError):
    """Wraps expected launcher failures that should print cleanly and exit 1."""


def _build_run(
    slash_command: str, session_prefix: str
) -> Callable[[argparse.Namespace], int]:
    """Return a run() function bound to the given slash command and session prefix."""

    def run(args: argparse.Namespace) -> int:
        try:
            workspace = resolve_workspace(args)
            require_claude_available()
            return launch_interactive(
                slash_command=slash_command,
                workspace=workspace,
                session_name=default_session_name(session_prefix, args.session_name),
                agent=args.agent,
                model=args.model,
            )
        except Exception as exc:  # noqa: BLE001
            return handle_launch_exception(exc)

    return run


def register_interactive_launcher(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    cmd_name: str,
    slash_command: str,
    help_text: str,
    session_prefix: str,
    parents: list[argparse.ArgumentParser] | None = None,
) -> argparse.ArgumentParser:
    """Register a standard interactive Claude launcher subcommand.

    All three daily workflow launchers (day-start, day-end, check-in) share
    identical argparse structure; only the command name, slash command string,
    help text, and session prefix differ.
    """
    parser = subparsers.add_parser(
        cmd_name,
        parents=parents or [],
        help=help_text,
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="Override the auto-generated session display name",
    )
    parser.add_argument(
        "--agent",
        default="work-planner",
        help="Agent to load (default: work-planner)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model alias or name (e.g., 'sonnet', 'opus')",
    )
    parser.set_defaults(func=_build_run(slash_command, session_prefix))
    return parser


def resolve_workspace(args: argparse.Namespace) -> Workspace:
    override = getattr(args, "workspace", None)
    try:
        return Workspace.discover_or_fail(override=Path(override) if override else None)
    except WorkspaceError as exc:
        raise SessionError(str(exc)) from exc


def require_claude_available() -> None:
    if not claude_cli.available():
        raise SessionError(
            "claude CLI not found on PATH. Install: " "https://claude.ai/download"
        )


def default_session_name(command: str, override: str | None = None) -> str:
    if override:
        return override
    return f"{command}-{date.today().isoformat()}"


def launch_interactive(
    *,
    slash_command: str,
    workspace: Workspace,
    session_name: str,
    agent: str = "work-planner",
    model: str | None = None,
) -> int:
    """Launch an interactive claude session driving `slash_command`.

    The slash command is passed as the opening prompt -- claude resolves
    it against `<workspace>/.claude/commands/` (materialized on `init`).
    """
    return claude_cli.spawn_interactive(
        prompt=slash_command,
        agent=agent,
        session_name=session_name,
        add_dirs=[workspace.root],
        model=model,
    )


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


def handle_launch_exception(exc: BaseException) -> int:
    """Translate subprocess / launcher failures into CLI exit codes with a user-visible message."""
    if isinstance(exc, SessionError):
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if isinstance(exc, claude_cli.ClaudeNotFoundError):
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if isinstance(exc, subprocess.TimeoutExpired):
        print(f"error: claude session timed out after {exc.timeout}s", file=sys.stderr)
        return 1
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        msg = f"error: claude exited {exc.returncode}"
        if stderr:
            msg = f"{msg}: {stderr}"
        print(msg, file=sys.stderr)
        return exc.returncode or 1
    raise
