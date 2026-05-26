"""doctor subcommand: verify and repair workspace contracts and dependencies."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from rich.console import Console as RichConsole
from rich.table import Table

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.console import Console


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
        help="Repair detected problems (preserves local edits).",
    )
    mode.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help="Force regenerate the managed .claude/ files (overwrites local edits).",
    )
    add_global_flags(p)
    p.set_defaults(func=run)
    return p


def _render_table(results: list[Any], console: RichConsole) -> None:
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
    from daily_driver.core.doctor import reset, run_checks
    from daily_driver.core.workspace import WorkspaceError

    console = RichConsole(stderr=True)

    try:
        workspace = resolve_workspace(args)
    except WorkspaceError:
        # doctor reports the path it tried rather than the discovery message,
        # so the user gets a copy-pasteable `init` invocation.
        workspace_override = getattr(args, "workspace", None)
        attempted = Path(workspace_override) if workspace_override else Path.cwd()
        Console.error(
            f"no workspace at {attempted} "
            f"(run 'daily-driver init {attempted}' to scaffold one)"
        )
        return 1

    if args.reset:
        reset(workspace)
        console.print("[green]✓[/green] workspace regenerated from package data")
        return 0

    if args.fix:
        from daily_driver.core import generate as generate_mod

        results = run_checks(workspace)
        _render_table(results, console)

        # Mirror core.doctor.fix(): only run generate when a fixable drift /
        # contract violation is present.
        needs_gen = any(
            r.fixable
            and r.status != "OK"
            and (r.name == "Workspace drift" or r.name.startswith("contract:"))
            for r in results
        )
        action: generate_mod.GenerationResult | None = None
        if needs_gen:
            action = generate_mod.generate(
                workspace, ignore_drift=True, force_overwrite=False
            )
        results = run_checks(workspace)

        if action is not None:
            console.print(
                f"\n[bold]Action:[/bold] regenerated {action.n_written} file"
                f"{'s' if action.n_written != 1 else ''} "
                f"(preserved {action.n_preserved} user-edited)"
            )
        console.print("\n[bold]After fix:[/bold]")
        _render_table(results, console)
        return 0 if all(r.status in ("OK", "WARNING") for r in results) else 1

    # Default: check and report.
    results = run_checks(workspace)
    _render_table(results, console)
    return 0 if all(r.status != "ERROR" for r in results) else 1
