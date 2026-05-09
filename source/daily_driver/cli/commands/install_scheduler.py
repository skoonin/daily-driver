"""install-scheduler subcommand: render + load LaunchAgent plists (macOS)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from daily_driver.cli._common import add_global_flags


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "install-scheduler",
        parents=parents,
        help="Install launchd scheduler plists (macOS only)",
    )
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.scheduler import SchedulerError, install_all
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
        installed = install_all(workspace)
    except SchedulerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not installed:
        console.print(
            "[yellow]No scheduler jobs configured.[/yellow]"
            " Add a `scheduler:` block to .dd-config.yaml to enable."
        )
        return 0

    console.print("[green]Installed launchd agents:[/green]")
    for label in installed:
        console.print(f"  • {label}")
    return 0
