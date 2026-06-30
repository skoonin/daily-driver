"""tracker subcommand: CRUD operations on the tracker YAML store."""

from __future__ import annotations

import argparse
import datetime
import json
from typing import Any

from rich.console import Console as RichConsole
from rich.table import Table

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.console import Console


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "tracker",
        parents=parents,
        help=(
            "Manage tracker entries "
            "(add, update, delete, prune, show, list, follow-ups, stats)"
        ),
    )

    nested = parser.add_subparsers(dest="tracker_action", metavar="<action>")

    # --- add ---
    p_add = nested.add_parser("add", parents=parents, help="Add a new tracker entry")
    p_add.add_argument(
        "-c",
        "--category",
        required=True,
        metavar="CAT",
        help="Entry category",
    )
    p_add.add_argument(
        "-T",
        "--title",
        required=True,
        metavar="TEXT",
        help="Entry title",
    )
    p_add.add_argument(
        "-s",
        "--status",
        default=None,
        metavar="STATUS",
        help="Initial status",
    )
    p_add.add_argument(
        "-t",
        "--tags",
        default=None,
        metavar="a,b",
        help="Comma-separated tags",
    )
    p_add.add_argument("-l", "--link", default=None, metavar="URL", help="Related URL")
    p_add.add_argument(
        "-N", "--note", default=None, metavar="TEXT", help="Free-text note"
    )
    p_add.add_argument(
        "--next-action", default=None, metavar="TEXT", help="Next action description"
    )
    p_add.add_argument(
        "-d",
        "--due",
        default=None,
        metavar="YYYY-MM-DD",
        help="Due date (ISO format)",
    )
    p_add.add_argument(
        "--extra",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="Extra key=value pairs (repeatable)",
    )
    add_global_flags(p_add)
    p_add.set_defaults(func=_run_add)

    # --- update ---
    p_update = nested.add_parser(
        "update", parents=parents, help="Update an existing entry"
    )
    p_update.add_argument("id", metavar="ID", help="Entry ID to update")
    p_update.add_argument(
        "-T",
        "--title",
        default=None,
        metavar="TEXT",
        help="New title",
    )
    p_update.add_argument(
        "-s",
        "--status",
        default=None,
        metavar="STATUS",
        help="New status",
    )
    p_update.add_argument(
        "-l", "--link", default=None, metavar="URL", help="New related URL"
    )
    p_update.add_argument(
        "-d",
        "--due",
        default=None,
        metavar="YYYY-MM-DD",
        help="New due date (ISO format)",
    )
    p_update.add_argument(
        "-N", "--note", default=None, metavar="TEXT", help="Append note"
    )
    p_update.add_argument(
        "--next-action", default=None, metavar="TEXT", help="Next action description"
    )
    p_update.add_argument(
        "-t",
        "--tags",
        default=None,
        metavar="a,b",
        help="Comma-separated tags (replaces existing)",
    )
    p_update.add_argument(
        "--extra",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="Extra key=value pairs (repeatable, merged into existing)",
    )
    add_global_flags(p_update)
    p_update.set_defaults(func=_run_update)

    # --- delete ---
    p_delete = nested.add_parser(
        "delete", parents=parents, help="Delete a single entry by ID"
    )
    p_delete.add_argument("id", metavar="ID", help="Entry ID to delete")
    add_global_flags(p_delete)
    p_delete.set_defaults(func=_run_delete)

    # --- prune ---
    p_prune = nested.add_parser(
        "prune",
        parents=parents,
        help="Bulk-delete entries by category, status, or age",
    )
    p_prune.add_argument(
        "-c",
        "--category",
        default=None,
        metavar="CAT",
        help="Match category",
    )
    p_prune.add_argument(
        "-s",
        "--status",
        default=None,
        metavar="STATUS",
        help="Match status",
    )
    p_prune.add_argument(
        "--older-than",
        default=None,
        metavar="SPEC",
        help=(
            "Match entries last updated before SPEC "
            "(today, yesterday, week, month, quarter, year, "
            "Nd, Nw, Nm, Ny, YYYY-MM-DD)"
        ),
    )
    p_prune.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        default=False,
        help="Print matching entries without deleting them",
    )
    p_prune.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit the matched/removed entry set as JSON",
    )
    add_global_flags(p_prune)
    p_prune.set_defaults(func=_run_prune)

    # --- show ---
    p_show = nested.add_parser(
        "show", parents=parents, help="Show a single tracker entry by ID"
    )
    p_show.add_argument("id", metavar="ID", help="Entry ID to display")
    p_show.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output",
    )
    add_global_flags(p_show)
    p_show.set_defaults(func=_run_show)

    # --- list ---
    p_list = nested.add_parser("list", parents=parents, help="List tracker entries")
    p_list.add_argument(
        "-c",
        "--category",
        default=None,
        metavar="CAT",
        help="Filter by category",
    )
    p_list.add_argument(
        "-s",
        "--status",
        default=None,
        metavar="FILTER",
        help="Filter by status",
    )
    p_list.add_argument(
        "-t", "--tag", default=None, metavar="TAG", help="Filter by tag"
    )
    p_list.add_argument(
        "--since",
        default=None,
        metavar="SPEC",
        help=(
            "Only list entries updated on/after SPEC "
            "(today, yesterday, week, month, quarter, year, Nd, Nw, Nm, Ny, YYYY-MM-DD)"
        ),
    )
    p_list.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output",
    )
    add_global_flags(p_list)
    p_list.set_defaults(func=_run_list)

    # --- follow-ups ---
    p_fu = nested.add_parser(
        "follow-ups", parents=parents, help="List entries with follow-up actions"
    )
    p_fu.add_argument(
        "--overdue",
        action="store_true",
        default=False,
        help="Restrict to overdue entries only",
    )
    p_fu.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output",
    )
    add_global_flags(p_fu)
    p_fu.set_defaults(func=_run_follow_ups)

    # --- stats ---
    p_stats = nested.add_parser(
        "stats", parents=parents, help="Show tracker statistics"
    )
    p_stats.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output",
    )
    add_global_flags(p_stats)
    p_stats.set_defaults(func=_run_stats)

    parser.set_defaults(func=run)
    return parser


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _parse_extras(raw: list[str] | None) -> dict[str, Any]:
    if not raw:
        return {}
    result: dict[str, Any] = {}
    for item in raw:
        if "=" not in item:
            Console.error(f"--extra argument must be KEY=VALUE, got: {item!r}")
            raise SystemExit(1)
        key, _, value = item.partition("=")
        result[key.strip()] = value
    return result


def _parse_due(raw: str | None) -> datetime.date | None:
    if raw is None:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        Console.error(f"--due must be YYYY-MM-DD, got: {raw!r}")
        raise SystemExit(1)


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    return entry.model_dump()


def _render_entries_table(entries: list[Any], console: RichConsole) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID")
    table.add_column("Category")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Tags")
    table.add_column("Due")
    for entry in entries:
        table.add_row(
            entry.id,
            entry.category,
            entry.title,
            entry.status,
            ", ".join(entry.tags),
            str(entry.due) if entry.due else "",
        )
    console.print(table)


def _run_add(args: argparse.Namespace, tracker: Any) -> int:
    extras = _parse_extras(args.extra)
    due = _parse_due(args.due)
    tags = _parse_tags(args.tags)

    entry = tracker.add(
        category=args.category,
        title=args.title,
        status=args.status,
        tags=tags if tags else None,
        link=args.link,
        notes=args.note,
        next_action=args.next_action,
        due=due,
        extras=extras if extras else None,
    )
    Console.success(f"Added {entry.id}: {entry.title}")
    return 0


def _run_update(args: argparse.Namespace, tracker: Any) -> int:
    changes: dict[str, Any] = {}
    if args.title is not None:
        changes["title"] = args.title
    if args.status is not None:
        changes["status"] = args.status
    if args.link is not None:
        changes["link"] = args.link
    if args.due is not None:
        changes["due"] = _parse_due(args.due)
    if args.next_action is not None:
        changes["next_action"] = args.next_action
    if args.tags is not None:
        changes["tags"] = _parse_tags(args.tags)
    extras = _parse_extras(args.extra)
    if extras:
        changes["extras"] = extras

    # Pass the raw note; the append happens inside Tracker.update's lock so a
    # concurrent update cannot clobber it via a stale read-built string.
    entry = tracker.update(args.id, append_note=args.note, **changes)
    Console.success(f"Updated {entry.id}: {entry.title}")
    return 0


def _run_delete(args: argparse.Namespace, tracker: Any) -> int:
    try:
        entry = tracker.delete(args.id)
    except KeyError as exc:
        Console.error(exc.args[0])
        return 1
    Console.success(f"Deleted {entry.id}: {entry.title}")
    return 0


def _run_prune(args: argparse.Namespace, tracker: Any) -> int:
    older_than = None
    if args.older_than is not None:
        from daily_driver.core.dates import parse_since

        try:
            older_than = parse_since(args.older_than)
        except ValueError as exc:
            Console.error(f"--older-than: {exc}")
            return 2
    try:
        removed = tracker.prune(
            category=args.category,
            status=args.status,
            older_than=older_than,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        Console.error(str(exc))
        return 2

    if getattr(args, "json", False):
        payload = {
            "dry_run": args.dry_run,
            "removed": [_entry_to_dict(e) for e in removed],
            "count": len(removed),
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2, default=str))
        return 0

    if not removed:
        Console.info("No matching entries.")
        return 0

    label = "Would delete" if args.dry_run else "Deleted"
    for entry in removed:
        Console.info(
            f"{label} {entry.id} [{entry.category}/{entry.status}]: {entry.title}"
        )
    Console.info(f"{label} {len(removed)} entr{'y' if len(removed) == 1 else 'ies'}.")
    return 0


def _run_show(args: argparse.Namespace, tracker: Any) -> int:
    try:
        entry = tracker.get(args.id)
    except KeyError as exc:
        Console.error(exc.args[0])
        return 1
    if getattr(args, "json", False):
        print(
            json.dumps(
                {"schema": 1, "data": {"entry": _entry_to_dict(entry)}},
                indent=2,
                default=str,
            )
        )
        return 0
    console = Console.get_user_console()
    table = Table(show_header=False, box=None, title=f"Entry {entry.id}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("ID", entry.id)
    table.add_row("Category", entry.category)
    table.add_row("Title", entry.title)
    table.add_row("Status", entry.status)
    table.add_row("Tags", ", ".join(entry.tags))
    table.add_row("Link", entry.link or "")
    table.add_row("Next action", entry.next_action or "")
    table.add_row("Due", str(entry.due) if entry.due else "")
    table.add_row("Created", entry.created_at.isoformat())
    table.add_row("Updated", entry.updated_at.isoformat())
    table.add_row("Notes", entry.notes)
    if entry.extras:
        for key, value in entry.extras.items():
            table.add_row(f"extras.{key}", str(value))
    console.print(table)
    return 0


def _run_list(args: argparse.Namespace, tracker: Any) -> int:
    since = None
    if args.since is not None:
        from daily_driver.core.dates import parse_since

        try:
            since = parse_since(args.since)
        except ValueError as exc:
            Console.error(str(exc))
            return 2
    entries = tracker.list(
        category=args.category,
        status=args.status,
        tag=args.tag,
        since=since,
    )
    if getattr(args, "json", False):
        payload = {
            "entries": [_entry_to_dict(e) for e in entries],
            "count": len(entries),
            "category_filter": args.category,
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2, default=str))
        return 0
    console = Console.get_user_console()
    _render_entries_table(entries, console)
    return 0


def _run_follow_ups(args: argparse.Namespace, tracker: Any) -> int:
    entries = tracker.follow_ups(overdue=args.overdue)
    if getattr(args, "json", False):
        payload = {
            "items": [_entry_to_dict(e) for e in entries],
            "count": len(entries),
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2, default=str))
        return 0
    console = Console.get_user_console()
    _render_entries_table(entries, console)
    return 0


def _run_stats(args: argparse.Namespace, tracker: Any) -> int:
    stats = tracker.stats()
    if getattr(args, "json", False):
        print(json.dumps({"schema": 1, "data": stats}, indent=2, default=str))
        return 0
    console = Console.get_user_console()
    table = Table(show_header=True, header_style="bold", title="Tracker Stats")
    table.add_column("Group")
    table.add_column("Value")
    table.add_column("Count")
    _DIMENSION_LABELS = {
        "total": "Total",
        "by_category": "By category",
        "by_status": "By status",
    }
    for dimension, counts in stats.items():
        label = _DIMENSION_LABELS.get(dimension, dimension)
        if isinstance(counts, dict):
            for key, count in counts.items():
                table.add_row(label, str(key), str(count))
        else:
            table.add_row(label, "", str(counts))
    console.print(table)
    return 0


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.tracker import Tracker
    from daily_driver.core.workspace import WorkspaceError

    if not hasattr(args, "func") or args.func is run:
        # No nested action selected — print help and exit.
        # Retrieve the tracker subparser's help via re-parse with -h.
        Console.error("usage: daily-driver tracker <action> ...")
        Console.error(
            "actions: add, update, delete, prune, show, list, follow-ups, stats"
        )
        return 2

    try:
        workspace = resolve_workspace(args)
    except WorkspaceError as exc:
        Console.error(str(exc))
        return 1

    tracker = Tracker(workspace)

    try:
        return args.func(args, tracker)
    except Exception as exc:  # noqa: BLE001
        Console.error(str(exc))
        return 1
