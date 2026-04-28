"""uninstall-scheduler subcommand: unload + remove LaunchAgent plists (macOS)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "uninstall-scheduler",
        parents=parents,
        help="Remove launchd scheduler plists (macOS only)",
    )
    parser.add_argument(
        "--keep-state",
        action="store_true",
        default=False,
        help="Retain mirrored plists under .daily-driver/state/launchd/",
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.scheduler import SchedulerError, uninstall_all
    from daily_driver.core.workspace import Workspace, WorkspaceError

    console = Console(stderr=False)

    workspace_override = getattr(args, "workspace", None)
    workspace_path = Path(workspace_override) if workspace_override else None
    try:
        workspace = Workspace.discover_or_fail(override=workspace_path)
    except WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        removed = uninstall_all(workspace, keep_state=args.keep_state)
    except SchedulerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not removed:
        console.print("[dim]No launchd agents were installed.[/dim]")
        return 0

    console.print("[green]Removed launchd agents:[/green]")
    for label in removed:
        console.print(f"  • {label}")
    if args.keep_state:
        console.print(
            "[dim]Mirrored plists retained under .daily-driver/state/launchd/[/dim]"
        )
    return 0
