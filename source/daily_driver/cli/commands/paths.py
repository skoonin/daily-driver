"""paths subcommand: print workspace-resolved paths for shell pipelines."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.cli.commands._utils import resolve_date
from daily_driver.core.console import Console
from daily_driver.core.daily_state import state_path as daily_state_path
from daily_driver.core.workspace import Workspace, WorkspaceError

_CHOICES = (
    "root",
    "output",
    "state",
    "ephemeral",
    "tracker",
    "daily",
    "daily-plan",
    "daily-notes",
    "daily-state",
)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "paths",
        parents=parents,
        help="Print resolved workspace paths (output, state, daily plan/notes)",
    )
    parser.add_argument(
        "kind",
        choices=_CHOICES,
        help="Which path to print",
    )
    parser.add_argument(
        "-d",
        "--date",
        default=None,
        help="ISO date (YYYY-MM-DD) for daily/daily-plan/daily-notes (default: today)",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit all paths as JSON instead of a single path",
    )
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def _daily_dir(workspace: Workspace, when: date) -> Path:
    return workspace.output_dir / f"{when.year:04d}" / f"{when.month:02d}"


def run(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args)
    except WorkspaceError as exc:
        Console.error(str(exc))
        return 1

    try:
        when = resolve_date(args.date)
    except ValueError as exc:
        raise SystemExit(f"error: invalid --date: {args.date}") from exc

    if getattr(args, "json", False):
        from daily_driver.core.tracker import Tracker

        daily = _daily_dir(workspace, when)
        payload = {
            "root": str(workspace.root),
            "output_dir": str(workspace.output_dir),
            "state_dir": str(workspace.state_dir),
            "ephemeral_dir": str(workspace.ephemeral_dir),
            "tracker": str(Tracker(workspace).path),
            "daily": str(daily),
            "daily_plan": str(daily / f"{when.isoformat()}-plan.md"),
            "daily_notes": str(daily / f"{when.isoformat()}-notes.md"),
            "daily_state": str(daily_state_path(workspace, when)),
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2))
        return 0

    if args.kind == "root":
        print(workspace.root)
    elif args.kind == "output":
        print(workspace.output_dir)
    elif args.kind == "state":
        print(workspace.state_dir)
    elif args.kind == "ephemeral":
        print(workspace.ephemeral_dir)
    elif args.kind == "tracker":
        from daily_driver.core.tracker import Tracker

        print(Tracker(workspace).path)
    elif args.kind == "daily":
        print(_daily_dir(workspace, when))
    elif args.kind == "daily-plan":
        print(_daily_dir(workspace, when) / f"{when.isoformat()}-plan.md")
    elif args.kind == "daily-notes":
        print(_daily_dir(workspace, when) / f"{when.isoformat()}-notes.md")
    elif args.kind == "daily-state":
        print(daily_state_path(workspace, when))
    return 0
