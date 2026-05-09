"""ensure-daily-dir subcommand: create today's output subdirectory and print plan path."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from daily_driver.cli._common import add_global_flags
from daily_driver.cli.commands._utils import resolve_date
from daily_driver.core.workspace import Workspace, WorkspaceError


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "ensure-daily-dir",
        parents=parents,
        help="Create today's daily directory and print the plan path",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="ISO date (YYYY-MM-DD); defaults to today",
    )
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    override = getattr(args, "workspace", None)
    try:
        workspace = Workspace.discover_or_fail(
            override=Path(override) if override else None
        )
    except WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        when = resolve_date(args.date)
    except ValueError:
        print(f"error: invalid --date: {args.date}", file=sys.stderr)
        return 2

    target = workspace.output_dir / f"{when.year:04d}" / f"{when.month:02d}"
    target.mkdir(parents=True, exist_ok=True)
    print(target / f"{when.isoformat()}-plan.md")
    return 0
