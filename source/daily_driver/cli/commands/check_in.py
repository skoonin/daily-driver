"""check-in subcommand: interactive mid-day review session.

When `ai.interactive.resume_check_in` is enabled in `.dd-config.yaml`, check-in
reattaches to the workspace's most-recent session (the same pointer `resume`
uses) through the provider's `resume` template, replaying the check-in prompt
into that conversation. When no session has been recorded yet, or the last one
came from another provider, it starts a fresh one; if the recorded session can
no longer be resumed, the provider reports it and check-in exits with its code.
The `--no-resume` flag forces a fresh session regardless of config.

After the session exits 0, `last_check_in_at` is recorded so the next /daily-driver:check-in
can bound `gather sessions` / `gather git` since the prior check-in (#35).
"""

from __future__ import annotations

import argparse

from daily_driver.cli._common import (
    add_global_flags,
    add_session_args,
    resolve_workspace,
)
from daily_driver.cli.commands._session import (
    add_launch_mode_arg,
    default_session_name,
    handle_launch_exception,
    handle_launch_mode,
    interactive_provider,
    launch_fresh_and_record,
    reattach_or_fresh,
)
from daily_driver.core import clock
from daily_driver.core.console import Console
from daily_driver.core.daily_state import (
    DailyState,
    DailyStateError,
    read_state,
    write_state,
)
from daily_driver.core.logging import get_logger
from daily_driver.core.workspace import Workspace

_SLASH_COMMAND = "/daily-driver:check-in"
_SESSION_PREFIX = "check-in"

_log = get_logger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "check-in",
        parents=parents,
        help="Interactive mid-day check-in session (runs /daily-driver:check-in)",
    )
    add_session_args(parser)
    add_launch_mode_arg(parser)
    parser.add_argument(
        "--no-resume",
        action="store_true",
        default=False,
        help="Start a fresh session instead of resuming the most recent session",
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
        # Scheduled firings post a clickable notification (or open a tab on
        # click) instead of spawning claude; focus mode suppresses them.
        diverted = handle_launch_mode(args, workspace, "check-in", respect_focus=True)
        if diverted is not None:
            return diverted
        provider = interactive_provider(workspace)

        session_name = args.session_name or default_session_name(_SESSION_PREFIX, None)
        should_resume = (
            not args.no_resume and workspace.config.ai.interactive.resume_check_in
        )
        launch = reattach_or_fresh if should_resume else launch_fresh_and_record
        rc = launch(
            workspace=workspace,
            provider=provider,
            command=_SLASH_COMMAND,
            session_name=session_name,
            agent=args.agent,
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
                Console.warning(
                    "check-in completed but failed to record "
                    f"last_check_in_at: {state_exc}"
                )
        return rc
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)
