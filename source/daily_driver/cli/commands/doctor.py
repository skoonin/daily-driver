"""CLI wrapper for the doctor subcommand."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "doctor",
        parents=parents,
        help="Check installation and workspace health",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Attempt to fix fixable problems.",
    )
    mode.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help="Force re-materialize .claude/ from package data.",
    )
    p.set_defaults(func=run)
    return p


def _render_table(results: list[Any], console: Console) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    _color = {"OK": "green", "WARNING": "yellow", "ERROR": "red"}

    for r in results:
        color = _color.get(r.status, "white")
        detail_cell = r.detail + (
            f"\n[dim italic]Hint: {r.fix_hint}[/dim italic]" if r.fix_hint else ""
        )
        table.add_row(r.name, f"[{color}]{r.status}[/{color}]", detail_cell)

    console.print(table)


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.doctor import fix, reset, run_checks
    from daily_driver.core.workspace import Workspace, WorkspaceError

    console = Console(stderr=True)

    workspace = None
    workspace_override = getattr(args, "workspace", None)
    workspace_path = Path(workspace_override) if workspace_override else None
    try:
        workspace = Workspace.discover_or_fail(override=workspace_path)
    except WorkspaceError:
        workspace = None

    if workspace is None:
        attempted = workspace_path if workspace_path is not None else Path.cwd()
        print(
            f"error: no workspace at {attempted} "
            f"(run 'daily-driver init {attempted}' to scaffold one)",
            file=sys.stderr,
        )
        return 1

    if args.reset:
        reset(workspace)
        console.print("[green]✓[/green] workspace re-materialized from package data")
        return 0

    if args.fix:
        results = run_checks(workspace)
        _render_table(results, console)
        results = fix(results, workspace)
        console.print("\n[bold]After fix:[/bold]")
        _render_table(results, console)
        return 0 if all(r.status in ("OK", "WARNING") for r in results) else 1

    # Default: check and report.
    results = run_checks(workspace)
    _render_table(results, console)
    return 0 if all(r.status != "ERROR" for r in results) else 1
