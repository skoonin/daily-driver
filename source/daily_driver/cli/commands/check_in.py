"""check-in subcommand: interactive mid-day review session via `claude`.

When `claude.resume_check_in` is enabled in `.dd-config.yaml` AND today's daily
state has a `last_day_start_session_id`, /check-in attempts
`claude --resume <uuid>` so the morning's planning context is already loaded.
On any resume failure (CalledProcessError), we fall back to a fresh session
and log a warning — never silently fail.

After the session exits 0, `last_check_in_at` is recorded so the next /check-in
can bound `gather sessions` / `gather git` since the prior check-in (#35).
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from daily_driver.cli._common import add_global_flags
from daily_driver.cli.commands._claude_session import (
    default_session_name,
    handle_launch_exception,
    require_claude_available,
    resolve_workspace,
)
from daily_driver.core import clock
from daily_driver.core.daily_state import (
    DailyState,
    DailyStateError,
    read_state,
    write_state,
)
from daily_driver.core.workspace import Workspace
from daily_driver.integrations import claude_cli

_SLASH_COMMAND = "/check-in"
_SESSION_PREFIX = "check-in"

_log = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "check-in",
        parents=parents,
        help="Interactive mid-day check-in session (runs /check-in via claude)",
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
    parser.add_argument(
        "--no-resume",
        action="store_true",
        default=False,
        help="Force a fresh claude session even if claude.resume_check_in is enabled",
    )
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def _record_check_in(workspace: Workspace) -> None:
    """Update last_check_in_at on today's state, preserving prior fields."""
    today = clock.today()
    now = clock.now()
    existing = read_state(workspace, today)
    if existing is None:
        state = DailyState(date=today, last_check_in_at=now)
    else:
        state = existing.model_copy(update={"last_check_in_at": now})
    write_state(workspace, state)


def run(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args)
        require_claude_available()

        today = clock.today()
        state = read_state(workspace, today)
        resume_id: str | None = None
        if (
            not args.no_resume
            and workspace.config.claude.resume_check_in
            and state is not None
            and state.last_day_start_session_id is not None
        ):
            resume_id = state.last_day_start_session_id

        session_name = args.session_name or default_session_name(_SESSION_PREFIX, None)

        try:
            rc = claude_cli.spawn_interactive(
                prompt=_SLASH_COMMAND,
                agent=args.agent,
                session_name=session_name,
                add_dirs=[workspace.root],
                model=args.model,
                resume_session_id=resume_id,
            )
        except subprocess.CalledProcessError as exc:
            if resume_id is None:
                raise
            _log.warning(
                "claude --resume %s failed (exit %s); starting fresh session",
                resume_id,
                exc.returncode,
            )
            print(
                f"warning: could not resume session {resume_id} "
                f"(claude exit {exc.returncode}); starting fresh",
                file=sys.stderr,
            )
            rc = claude_cli.spawn_interactive(
                prompt=_SLASH_COMMAND,
                agent=args.agent,
                session_name=session_name,
                add_dirs=[workspace.root],
                model=args.model,
            )

        if rc == 0:
            # Bookkeeping failure must NOT turn a successful interactive
            # session into a failed CLI exit. Warn and return rc=0 so the
            # user (and shell) sees the truth.
            try:
                _record_check_in(workspace)
            except (DailyStateError, OSError) as state_exc:
                _log.warning("check-in succeeded but state write failed: %s", state_exc)
                print(
                    "warning: check-in completed but failed to record "
                    f"last_check_in_at: {state_exc}",
                    file=sys.stderr,
                )
        return rc
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)
