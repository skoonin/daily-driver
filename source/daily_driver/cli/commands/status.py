"""status subcommand: workspace and tracker summary dashboard."""

from __future__ import annotations

import argparse
import datetime
import importlib.resources
import json
from typing import Any

from rich.table import Table

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core import clock
from daily_driver.core.console import Console

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
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output instead of Rich tables",
    )
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    return entry.model_dump()


def _read_package_template(name: str) -> str | None:
    """Return the bundled template content, or None if not found."""
    try:
        return (
            importlib.resources.files("daily_driver.resources.templates")
            .joinpath(name)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None


def _detect_setup_gaps(workspace: Any, all_entries: list[Any]) -> list[dict[str, str]]:
    """Surface common first-run config gaps so a fresh workspace looks
    different from a quiet day. Each gap has a short id (machine-readable)
    and a human-readable message.
    """
    gaps: list[dict[str, str]] = []

    context_path = workspace.root / "context.md"
    if not context_path.exists():
        gaps.append(
            {
                "id": "context_missing",
                "message": "context.md is missing — run `daily-driver init`",
            }
        )
    else:
        template = _read_package_template("context.md")
        if template is not None:
            actual = context_path.read_text(encoding="utf-8")
            if actual.strip() == template.strip():
                gaps.append(
                    {
                        "id": "context_unedited",
                        "message": (
                            "context.md is still the default template — "
                            "edit it to describe yourself, your goals, and your constraints"
                        ),
                    }
                )

    voice_path = workspace.root / "voice-profile.md"
    if not voice_path.exists() or not voice_path.read_text(encoding="utf-8").strip():
        gaps.append(
            {
                "id": "voice_profile_empty",
                "message": (
                    "voice-profile.md is empty — run `daily-driver voice-update "
                    "--from <path>` to seed it from writing samples"
                ),
            }
        )
    else:
        template = _read_package_template("voice-profile.md")
        if template is not None:
            actual = voice_path.read_text(encoding="utf-8")
            if actual.strip() == template.strip():
                gaps.append(
                    {
                        "id": "voice_profile_template",
                        "message": (
                            "voice-profile.md is still the default template — "
                            "run `daily-driver voice-update --from <path>` to seed it"
                        ),
                    }
                )

    git_paths = workspace.config.gather.git.search_paths
    if not git_paths:
        gaps.append(
            {
                "id": "gather_git_unset",
                "message": (
                    "gather.git.search_paths is empty — `gather git` will only "
                    "scan the current directory. Add repo paths to .dd-config.yaml."
                ),
            }
        )

    if not all_entries:
        gaps.append(
            {
                "id": "tracker_empty",
                "message": (
                    "tracker is empty — `daily-driver tracker add --category task "
                    "--title ...` to record your first item"
                ),
            }
        )
    elif all(e.id.startswith(("test-", "test_")) for e in all_entries):
        gaps.append(
            {
                "id": "tracker_only_fixtures",
                "message": (
                    "tracker only contains test fixtures — "
                    "`daily-driver tracker prune --category test` to clear them"
                ),
            }
        )

    return gaps


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.statuses import normalize_status
    from daily_driver.core.tracker import Tracker, terminal_statuses_for
    from daily_driver.core.workspace import WorkspaceError

    try:
        workspace = resolve_workspace(args)
    except WorkspaceError as exc:
        Console.error(str(exc))
        return 1

    tracker = Tracker(workspace)
    terminal_statuses = terminal_statuses_for(workspace.config.tracker)

    try:
        all_entries = tracker.list()
    except Exception as exc:  # noqa: BLE001
        Console.error(f"loading tracker: {exc}")
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
        if normalize_status(e.status) not in terminal_statuses
        and _to_utc(e.updated_at) < stale_threshold
    ]

    # Recent: updated within last 7 days, sorted most-recent first
    recent = sorted(
        [e for e in all_entries if _to_utc(e.updated_at) >= recent_threshold],
        key=lambda e: _to_utc(e.updated_at),
        reverse=True,
    )

    setup_gaps = _detect_setup_gaps(workspace, all_entries)

    if args.json:
        payload = {
            "totals": {
                "total": total,
                "by_category": by_category,
                "by_status": by_status,
            },
            "stalled": [_entry_to_dict(e) for e in stalled],
            "recent": [_entry_to_dict(e) for e in recent],
            "setup_gaps": setup_gaps,
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2, default=str))
        return 0

    console = Console.get_user_console()

    # --- Setup gaps (printed first so it's visible above the tables) ---
    if setup_gaps:
        console.print(
            "[bold yellow]Setup gaps "
            "(workspace looks unconfigured, not quiet):[/bold yellow]"
        )
        for gap in setup_gaps:
            console.print(f"  • {gap['message']}")
        console.print("")

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
    # core.clock.now writes local-aware timestamps; a hand-edited naive value
    # is local wall-clock time. Interpret it as local (.astimezone()), not UTC,
    # so recent/stalled math isn't skewed by the local UTC offset.
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt
