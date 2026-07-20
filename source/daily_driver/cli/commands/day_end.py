"""day-end subcommand: interactive end-of-day review session via `claude`.

Like day-start, day-end mints a `--session-id` UUID and records it as the
workspace's most-recent session so a lost tab can be reattached later via
`daily-driver resume` (or a subsequent `check-in`).
"""

from __future__ import annotations

import argparse

from daily_driver.cli._common import (
    add_global_flags,
    add_session_args,
    resolve_workspace,
)
from daily_driver.cli.commands._claude_session import (
    add_launch_mode_arg,
    default_session_name,
    handle_launch_exception,
    handle_launch_mode,
    launch_fresh_and_record,
    require_claude_available,
    resolve_interactive_model,
)

_SLASH_COMMAND = "/daily-driver:day-end"
_SESSION_PREFIX = "day-end"


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "day-end",
        parents=parents,
        help="Interactive end-of-day review session (runs /daily-driver:day-end via claude)",
    )
    add_session_args(parser)
    add_launch_mode_arg(parser)
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args)
        diverted = handle_launch_mode(args, workspace, "day-end")
        if diverted is not None:
            return diverted
        require_claude_available()

        session_name = default_session_name(_SESSION_PREFIX, args.session_name)
        return launch_fresh_and_record(
            workspace=workspace,
            prompt=_SLASH_COMMAND,
            session_name=session_name,
            agent=args.agent,
            model=resolve_interactive_model(workspace, args.model),
        )
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)
