"""resume subcommand: reattach to the workspace's most recent Claude session.

When a day-start / day-end / check-in tab is closed or lost, `resume` reattaches
to the last session started here via `claude --resume <uuid>` — through the same
launcher path as the other commands, so the workspace's interactive model, agent,
and `--add-dir` still apply (unlike a bare `claude -c`). The most-recent session
id is read from the workspace pointer that every launcher records.

This is a manual recovery command: it takes no `--launch` scheduler mode. With
no prior session recorded it errors cleanly rather than opening an untethered
fresh session. If the recorded session can no longer be resumed, claude reports
that ("No conversation found with session ID") and resume exits with claude's
code — run day-start to begin a fresh session.
"""

from __future__ import annotations

import argparse

from daily_driver.cli._common import (
    add_global_flags,
    add_session_args,
    resolve_workspace,
)
from daily_driver.cli.commands._claude_session import (
    SessionError,
    default_session_name,
    handle_launch_exception,
    reattach_or_fresh,
    require_claude_available,
    resolve_interactive_model,
)
from daily_driver.core.session_pointer import read_pointer

_SESSION_PREFIX = "resume"


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "resume",
        parents=parents,
        help="Reattach to the most recent Claude session for this workspace",
    )
    add_session_args(parser)
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args)
        require_claude_available()

        pointer = read_pointer(workspace)
        if pointer is None or pointer.last_session_id is None:
            raise SessionError(
                "no prior session to resume — run day-start, day-end, or "
                "check-in first"
            )

        session_name = default_session_name(_SESSION_PREFIX, args.session_name)
        # No opening prompt: reattaching drops the user back into the existing
        # conversation rather than re-running a slash command. If the id can no
        # longer be resumed, claude's own error and exit code propagate.
        return reattach_or_fresh(
            workspace=workspace,
            prompt=None,
            session_name=session_name,
            agent=args.agent,
            model=resolve_interactive_model(workspace, args.model),
        )
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)
