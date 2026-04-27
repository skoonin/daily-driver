"""status subcommand: workspace and tracker summary dashboard."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from daily_driver.core import clock

# Statuses considered terminal — entries in these are excluded from "stalled".
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "done", "closed", "dropped", "withdrawn", "rejected"}
)

_STALE_DAYS = 14
_RECENT_DAYS = 7


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "status",
        parents=parents,
        help="Show workspace status and tracker summary",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output instead of Rich tables",
    )
    parser.set_defaults(func=run)
    return parser


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    return entry.model_dump()


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.tracker import Tracker
    from daily_driver.core.workspace import Workspace, WorkspaceError

    workspace_override = getattr(args, "workspace", None)
    workspace_path = Path(workspace_override) if workspace_override else None
    try:
        workspace = Workspace.discover_or_fail(override=workspace_path)
    except WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    tracker = Tracker(workspace)

    try:
        all_entries = tracker.list()
    except Exception as exc:  # noqa: BLE001
        print(f"error loading tracker: {exc}", file=sys.stderr)
        return 1

    now = clock.now()
    stale_threshold = now - datetime.timedelta(days=_STALE_DAYS)
    recent_threshold = now - datetime.timedelta(days=_RECENT_DAYS)

    # Totals
    total = len(all_entries)
    by_category: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for entry in all_entries:
        by_category[entry.category] = by_category.get(entry.category, 0) + 1
        by_status[entry.status] = by_status.get(entry.status, 0) + 1

    # Stalled: not terminal AND not touched in >14 days
    stalled = [
        e
        for e in all_entries
        if e.status not in _TERMINAL_STATUSES
        and _to_utc(e.updated_at) < stale_threshold
    ]

    # Recent: updated within last 7 days, sorted most-recent first
    recent = sorted(
        [e for e in all_entries if _to_utc(e.updated_at) >= recent_threshold],
        key=lambda e: _to_utc(e.updated_at),
        reverse=True,
    )

    if args.json:
        payload = {
            "totals": {
                "total": total,
                "by_category": by_category,
                "by_status": by_status,
            },
            "stalled": [_entry_to_dict(e) for e in stalled],
            "recent": [_entry_to_dict(e) for e in recent],
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2, default=str))
        return 0

    console = Console(stderr=False)

    # --- Totals table ---
    totals_table = Table(show_header=True, header_style="bold", title="Tracker Totals")
    totals_table.add_column("Dimension")
    totals_table.add_column("Value")
    totals_table.add_column("Count")
    totals_table.add_row("total", "", str(total))
    for cat, count in sorted(by_category.items()):
        totals_table.add_row("category", cat, str(count))
    for status, count in sorted(by_status.items()):
        totals_table.add_row("status", status, str(count))
    console.print(totals_table)

    # --- Stalled table ---
    stalled_table = Table(
        show_header=True,
        header_style="bold",
        title=f"Stalled (no update in >{_STALE_DAYS}d, non-terminal)",
    )
    stalled_table.add_column("ID")
    stalled_table.add_column("Category")
    stalled_table.add_column("Title")
    stalled_table.add_column("Status")
    stalled_table.add_column("Last Updated")
    for entry in stalled:
        stalled_table.add_row(
            entry.id,
            entry.category,
            entry.title,
            entry.status,
            str(entry.updated_at.date()),
        )
    if not stalled:
        console.print("[dim]Stalled: none[/dim]")
    else:
        console.print(stalled_table)

    # --- Recent table ---
    recent_table = Table(
        show_header=True,
        header_style="bold",
        title=f"Recent Activity (last {_RECENT_DAYS}d)",
    )
    recent_table.add_column("ID")
    recent_table.add_column("Category")
    recent_table.add_column("Title")
    recent_table.add_column("Status")
    recent_table.add_column("Updated")
    for entry in recent:
        recent_table.add_row(
            entry.id,
            entry.category,
            entry.title,
            entry.status,
            str(entry.updated_at.date()),
        )
    if not recent:
        console.print("[dim]Recent activity: none[/dim]")
    else:
        console.print(recent_table)

    return 0


def _to_utc(dt: datetime.datetime) -> datetime.datetime:
    # Normalize naive datetimes (assumed UTC) to offset-aware for comparison.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt
