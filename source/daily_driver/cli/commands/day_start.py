"""day-start subcommand: interactive morning planning session.

Pre-launch the program writes:
  1. a plan stub at <output_dir>/YYYY/MM/YYYY-MM-DD-plan.md (only if absent —
     never clobbers user or AI edits if day-start is re-run mid-day);
  2. the day's state YAML with `last_day_start_at` and `late_day`.

Then the interactive provider is launched with a recorded session ID, so
check-in and resume can continue the same conversation later in the day.
"""

from __future__ import annotations

import argparse
from datetime import date as date_cls
from pathlib import Path

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
)
from daily_driver.core import clock
from daily_driver.core.daily_state import (
    DailyState,
    is_late_day,
    read_state,
    write_state,
)
from daily_driver.core.workspace import Workspace, daily_file

_SLASH_COMMAND = "/daily-driver:day-start"
_SESSION_PREFIX = "day-cycle"


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "day-start",
        parents=parents,
        help="Interactive morning planning session (runs /daily-driver:day-start)",
    )
    add_session_args(parser)
    add_launch_mode_arg(parser)
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def _plan_path(workspace: Workspace, day: date_cls) -> Path:
    return daily_file(workspace.output_dir, day, "plan")


def _write_plan_stub_if_absent(path: Path, day: date_cls) -> None:
    """Write a minimal plan stub. Idempotent: never overwrites existing content."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    stub = (
        "---\n"
        f"date: {day.isoformat()}\n"
        f"generated_at: {clock.now().strftime('%H:%M')}\n"
        "plan_items: []\n"
        "carry_forward: []\n"
        "---\n"
        "\n"
        "<!-- Plan stub written by `daily-driver day-start`. "
        "Claude will populate this file. -->\n"
    )
    path.write_text(stub, encoding="utf-8")


def _record_day_start(workspace: Workspace, day: date_cls) -> None:
    """Merge a new day-start into today's state, preserving prior fields.

    The session id is recorded workspace-wide by `launch_fresh_and_record` (the
    resume source); per-day state only tracks when day-start ran and whether it
    was late.
    """
    started_at = clock.now()
    late = is_late_day(workspace, started_at)
    existing = read_state(workspace, day)
    if existing is None:
        state = DailyState(
            date=day,
            last_day_start_at=started_at,
            late_day=late,
        )
    else:
        state = existing.model_copy(
            update={
                "last_day_start_at": started_at,
                "late_day": late,
            }
        )
    write_state(workspace, state)


def run(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args)
        # Scheduler firings divert before any plan-stub or state write — the
        # relaunched interactive run in the fresh tab performs those itself.
        diverted = handle_launch_mode(args, workspace, "day-start")
        if diverted is not None:
            return diverted
        provider = interactive_provider(workspace)

        today = clock.today()

        _write_plan_stub_if_absent(_plan_path(workspace, today), today)
        _record_day_start(workspace, today)

        session_name = args.session_name or default_session_name(_SESSION_PREFIX, None)
        return launch_fresh_and_record(
            workspace=workspace,
            provider=provider,
            command=_SLASH_COMMAND,
            session_name=session_name,
            agent=args.agent,
            model=args.model,
        )
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)
