"""jobs subcommand: run job-board scraper or show run status."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from daily_driver.cli._common import add_global_flags


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "jobs",
        parents=parents,
        help="Job-board scraper: run the scraper or inspect its last run",
    )

    nested = parser.add_subparsers(dest="jobs_action", metavar="<action>")

    p_run = nested.add_parser("run", parents=parents, help="Scrape enabled job boards")
    p_run.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print matches without writing to CSV",
    )
    p_run.add_argument(
        "--backfill",
        action="store_true",
        help="Re-enrich empty fields in existing jobs.csv rows",
    )
    add_global_flags(p_run)
    p_run.set_defaults(func=_run_scrape)

    p_status = nested.add_parser(
        "status", parents=parents, help="Show last-run metadata and job counts"
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output wrapped in {schema, data}",
    )
    add_global_flags(p_status)
    p_status.set_defaults(func=_run_status)

    p_prune = nested.add_parser(
        "prune",
        parents=parents,
        help="Move stale rows from jobs.csv to jobs.archive.csv",
    )
    p_prune.add_argument(
        "--older-than",
        required=True,
        metavar="SPEC",
        help=(
            "Prune rows last seen before SPEC "
            "(today, week, month, quarter, year, Nd, Nw, Nm, Ny, YYYY-MM-DD)"
        ),
    )
    p_prune.add_argument(
        "--status",
        action="append",
        default=None,
        metavar="STATUS",
        help=(
            "Status to prune (repeatable). Default: dropped, rejected, closed. "
            "Use --status active to nuke stale active rows."
        ),
    )
    p_prune.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print prune candidates without writing to disk",
    )
    add_global_flags(p_prune)
    p_prune.set_defaults(func=_run_prune)

    parser.set_defaults(func=run)
    return parser


def _resolve_output_dir(workspace) -> Path:  # type: ignore[no-untyped-def]
    output_dir_raw = workspace.config.daily_driver.output_dir
    output_dir = Path(output_dir_raw).expanduser()
    if not output_dir.is_absolute():
        output_dir = (workspace.root / output_dir).resolve()
    return output_dir


def _run_scrape(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from daily_driver.scraper import run as run_scrape
    from daily_driver.scraper import run_backfill

    legacy = workspace.root / "config.yaml"
    if legacy.exists():
        print(
            f"error: {legacy} is a legacy config file. "
            "Move settings to plugins.job_search in .dd-config.yaml. "
            "See docs/configuration.md.",
            file=sys.stderr,
        )
        return 1

    if not logging.getLogger().handlers:
        verbosity = getattr(args, "verbose", 0) or 0
        level = logging.DEBUG if verbosity >= 2 else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stdout,
        )

    plugins = workspace.config.plugins
    if plugins is None or plugins.job_search is None:
        print(
            "error: no plugins.job_search found in .dd-config.yaml. "
            "See docs/configuration.md.",
            file=sys.stderr,
        )
        return 1

    config = {
        "job_search": plugins.job_search.model_dump(exclude_none=True, mode="json")
    }
    output_dir = _resolve_output_dir(workspace)
    csv_path = output_dir / "jobs.csv"

    if args.backfill:
        run_backfill(config, csv_path)
        return 0

    return run_scrape(config, output_dir, dry_run=args.dry_run)


def _run_prune(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from rich.console import Console
    from rich.table import Table

    from daily_driver.core.dates import parse_since
    from daily_driver.core.jobs_archive import DEFAULT_PRUNE_STATUSES, prune

    try:
        cutoff = parse_since(args.older_than)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.status:
        statuses = tuple(s.strip().lower() for s in args.status if s.strip())
    else:
        statuses = DEFAULT_PRUNE_STATUSES

    output_dir = _resolve_output_dir(workspace)
    csv_path = output_dir / "jobs.csv"

    candidates, archived = prune(
        csv_path, cutoff=cutoff, statuses=statuses, dry_run=args.dry_run
    )

    console = Console(stderr=False)
    if not candidates:
        console.print("[dim]No rows match prune criteria.[/dim]")
        return 0

    table = Table(
        title=f"Prune candidates ({'dry-run' if args.dry_run else 'archived'})",
        show_header=True,
    )
    table.add_column("Company")
    table.add_column("Status")
    table.add_column("Date Last Seen")
    table.add_column("Role")
    for row in candidates:
        table.add_row(
            row.get("Company", ""),
            row.get("Status", ""),
            row.get("Date Last Seen", "") or row.get("Date Found", ""),
            row.get("Role", ""),
        )
    console.print(table)
    if args.dry_run:
        console.print(f"[yellow]Dry-run: {len(candidates)} would be pruned.[/yellow]")
    else:
        console.print(f"[green]Archived {archived} rows to jobs.archive.csv.[/green]")
    return 0


def _run_status(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from rich.console import Console
    from rich.table import Table

    from daily_driver.core.scraper_status import build_status

    output_dir = _resolve_output_dir(workspace)
    status = build_status(output_dir)

    emit_json = getattr(args, "json", False)
    if emit_json:
        print(json.dumps({"schema": 1, "data": status}, indent=2))
        return 0

    console = Console(stderr=False)

    last_run = status["last_run"]
    if last_run is None:
        console.print("[yellow]No scraper run recorded yet.[/yellow]")
    else:
        console.print(f"[bold]Last run:[/bold] {last_run.get('started_at', '?')}")
        console.print(f"  New jobs:       {last_run.get('new_jobs', '?')}")
        sources_ok = last_run.get("sources_ok") or []
        sources_failed = last_run.get("sources_failed") or []
        console.print(f"  Sources OK:     {', '.join(sources_ok) or 'none'}")
        if sources_failed:
            console.print(f"  [red]Sources failed:[/red] {', '.join(sources_failed)}")

    counts = status["job_counts"]
    if counts:
        table = Table(title="Jobs by status", show_header=True)
        table.add_column("Status")
        table.add_column("Count", justify="right")
        for state, count in sorted(counts.items()):
            table.add_row(state, str(count))
        console.print(table)
        console.print(
            f"Awaiting action (applied/interviewing): {status['awaiting_action']}"
        )
    else:
        console.print("[dim]No jobs.csv found.[/dim]")

    return 0


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.workspace import Workspace, WorkspaceError

    if not hasattr(args, "func") or args.func is run:
        print("usage: daily-driver jobs <action> ...", file=sys.stderr)
        print("actions: run, status, prune", file=sys.stderr)
        return 2

    workspace_override = getattr(args, "workspace", None)
    workspace_path = Path(workspace_override) if workspace_override else None
    try:
        workspace = Workspace.discover_or_fail(override=workspace_path)
    except WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        return args.func(args, workspace)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
