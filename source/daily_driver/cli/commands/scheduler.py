"""scheduler subcommand: install / uninstall / status for launchd plists (macOS)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from rich.console import Console as RichConsole

from daily_driver.cli._common import add_global_flags
from daily_driver.core.console import Console


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "scheduler",
        parents=parents,
        help="Install, uninstall, or check the macOS background scheduler",
    )
    nested = parser.add_subparsers(dest="scheduler_action", metavar="<action>")

    p_install = nested.add_parser(
        "install",
        parents=parents,
        help="Install launchd agents for configured jobs",
    )
    add_global_flags(p_install)
    p_install.set_defaults(func=_run_install)

    p_uninstall = nested.add_parser(
        "uninstall",
        parents=parents,
        help="Remove all daily-driver launchd agents",
    )
    add_global_flags(p_uninstall)
    p_uninstall.set_defaults(func=_run_uninstall)

    p_status = nested.add_parser(
        "status",
        parents=parents,
        help="Show configured jobs and whether each is installed",
    )
    p_status.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output",
    )
    add_global_flags(p_status)
    p_status.set_defaults(func=_run_status)

    parser.set_defaults(func=run)
    return parser


def _resolve_workspace(args: argparse.Namespace) -> Any:
    from daily_driver.core.workspace import Workspace, WorkspaceError

    workspace_override = getattr(args, "workspace", None)
    workspace_path = Path(workspace_override) if workspace_override else None
    try:
        return Workspace.discover_or_fail(override=workspace_path)
    except WorkspaceError as exc:
        Console.error(str(exc))
        return None


def _run_install(args: argparse.Namespace) -> int:
    from daily_driver.core.scheduler import SchedulerError, install_all

    console = RichConsole(stderr=False)
    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1

    try:
        installed = install_all(workspace)
    except SchedulerError as exc:
        Console.error(str(exc))
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


def _run_uninstall(args: argparse.Namespace) -> int:
    from daily_driver.core.scheduler import SchedulerError, uninstall_all

    console = RichConsole(stderr=False)
    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1

    try:
        removed = uninstall_all(workspace)
    except SchedulerError as exc:
        Console.error(str(exc))
        return 1

    if not removed:
        console.print("[dim]No launchd agents were installed.[/dim]")
        return 0

    console.print("[green]Removed launchd agents:[/green]")
    for label in removed:
        console.print(f"  • {label}")
    return 0


def _run_status(args: argparse.Namespace) -> int:
    import json as _json

    from rich.table import Table

    from daily_driver.core.scheduler import SchedulerError, build_jobs
    from daily_driver.integrations import launchd as launchd_int

    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1

    try:
        jobs = build_jobs(workspace)
    except SchedulerError as exc:
        Console.error(str(exc))
        return 1

    state_dir = workspace.ephemeral_dir / "launchd"
    rows = []
    for job in jobs:
        plist_path = launchd_int.plist_path(job.label)
        installed = plist_path.exists()
        mirrored = (state_dir / f"{job.label}.plist").exists()
        rows.append(
            {
                "label": job.label,
                "installed": installed,
                "state_mirror": mirrored,
                "plist_path": str(plist_path),
            }
        )

    if getattr(args, "json", False):
        print(_json.dumps({"schema": 1, "data": rows}, indent=2))
        return 0

    console = RichConsole(stderr=False)
    if not rows:
        console.print(
            "[dim]No scheduler jobs configured.[/dim]"
            " Add a `scheduler:` block to .dd-config.yaml to enable."
        )
        return 0

    table = Table(show_header=True, header_style="bold", title="Scheduler status")
    table.add_column("Job")
    table.add_column("Installed")
    table.add_column("Plist path")
    for row in rows:
        marker = "[green]yes[/green]" if row["installed"] else "[dim]no[/dim]"
        table.add_row(row["label"], marker, row["plist_path"])
    console.print(table)
    return 0


def run(args: argparse.Namespace) -> int:
    """Bare `scheduler` (no action) prints help and returns 2."""
    if not hasattr(args, "func") or args.func is run:
        Console.error("usage: daily-driver scheduler {install,uninstall,status} ...")
        return 2
    return args.func(args)
