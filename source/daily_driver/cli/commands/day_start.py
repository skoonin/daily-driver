"""day-start subcommand: interactive morning planning session via `claude`.

Pre-launch the program writes:
  1. a plan stub at <output_dir>/YYYY/MM/YYYY-MM-DD-plan.md (only if absent —
     never clobbers user/claude edits if day-start is re-run mid-day);
  2. the day's state YAML with a freshly-minted `session_id` (UUID) and
     `last_day_start_at`.

Then `claude --session-id <uuid>` is launched so /check-in (F3) can resume the
same conversation later in the day.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import date as date_cls
from pathlib import Path

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
    is_late_day,
    read_state,
    write_state,
)
from daily_driver.core.workspace import Workspace
from daily_driver.integrations import claude_cli

_SLASH_COMMAND = "/day-start"
_SESSION_PREFIX = "day-cycle"


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "day-start",
        parents=parents,
        help="Interactive morning planning session (runs /day-start via claude)",
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
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def _plan_path(workspace: Workspace, day: date_cls) -> Path:
    return (
        workspace.output_dir
        / f"{day.year:04d}"
        / f"{day.month:02d}"
        / f"{day.isoformat()}-plan.md"
    )


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


def _record_day_start(workspace: Workspace, day: date_cls, session_id: str) -> None:
    """Merge a new day-start into today's state, preserving prior fields."""
    started_at = clock.now()
    late = is_late_day(workspace, started_at)
    existing = read_state(workspace, day)
    if existing is None:
        state = DailyState(
            date=day,
            last_day_start_session_id=session_id,
            last_day_start_at=started_at,
            late_day=late,
        )
    else:
        state = existing.model_copy(
            update={
                "last_day_start_session_id": session_id,
                "last_day_start_at": started_at,
                "late_day": late,
            }
        )
    write_state(workspace, state)


def run(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args)
        require_claude_available()

        today = clock.today()
        session_id = str(uuid.uuid4())

        _write_plan_stub_if_absent(_plan_path(workspace, today), today)
        _record_day_start(workspace, today, session_id)

        session_name = args.session_name or default_session_name(_SESSION_PREFIX, None)
        return claude_cli.spawn_interactive(
            prompt=_SLASH_COMMAND,
            agent=args.agent,
            session_name=session_name,
            add_dirs=[workspace.root],
            model=args.model,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)
