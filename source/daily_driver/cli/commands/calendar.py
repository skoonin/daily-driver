"""calendar subcommand: write today's plan time blocks to a local Calendar (macOS)."""

from __future__ import annotations

import argparse

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.console import Console


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "calendar",
        parents=parents,
        help="Write today's plan time blocks to a local macOS Calendar",
    )
    nested = parser.add_subparsers(dest="calendar_action", metavar="<action>")

    p_sync = nested.add_parser(
        "sync",
        parents=parents,
        help="Sync today's plan time blocks into the configured Calendar",
    )
    add_global_flags(p_sync)
    p_sync.set_defaults(func=_run_sync)

    parser.set_defaults(func=run)
    return parser


def _run_sync(args: argparse.Namespace) -> int:
    from daily_driver.cli.commands.day_start import _plan_path
    from daily_driver.core import clock
    from daily_driver.core.plan import read_plan_time_blocks
    from daily_driver.core.workspace import WorkspaceError
    from daily_driver.integrations import calendar_write

    console = Console.get_user_console()
    try:
        workspace = resolve_workspace(args)
    except WorkspaceError as exc:
        Console.error(str(exc))
        return 1

    if not workspace.config.calendar.sync_enabled:
        console.print(
            "[dim]Calendar sync is disabled."
            " Set `calendar.sync_enabled: true` in .dd-config.yaml to enable.[/dim]"
        )
        return 0

    today = clock.today()
    events = read_plan_time_blocks(_plan_path(workspace, today), day=today)
    if not events:
        console.print("[dim]No plan time blocks to sync.[/dim]")
        return 0

    calendar_name = workspace.config.calendar.plan_calendar_name
    result = calendar_write.write_day(calendar_name, today, events)
    if not result.ok:
        # Best-effort: a write failure must not abort the day-start flow.
        console.print(
            f"[yellow]Calendar sync skipped: {result.reason}.[/yellow]"
            " See docs/dev/developer.md 'Calendar (write) setup'."
        )
        return 0

    console.print(
        f"[green]Synced {result.written} time block(s)"
        f" to calendar '{calendar_name}'.[/green]"
    )
    return 0


def run(args: argparse.Namespace) -> int:
    """Bare `calendar` (no action) prints help and returns 2."""
    if not hasattr(args, "func") or args.func is run:
        Console.error("usage: daily-driver calendar {sync} ...")
        return 2
    return args.func(args)
