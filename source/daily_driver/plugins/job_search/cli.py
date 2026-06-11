"""jobs subcommand: job-search workflows (run, status, prune)."""

from __future__ import annotations

import argparse
import json

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.console import Console


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "jobs",
        parents=parents,
        help="Job search: scrape boards, inspect status, prune stale rows",
    )

    nested = parser.add_subparsers(dest="jobs_action", metavar="<action>")

    p_run = nested.add_parser(
        "run",
        parents=parents,
        help="Run the configured job-board search pipeline",
    )
    p_run.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print results without writing to jobs.csv",
    )
    p_run.add_argument(
        "--backfill",
        action="store_true",
        help="Re-enrich empty fields in existing jobs.csv rows",
    )
    p_run.add_argument(
        "--no-enrich",
        action="store_true",
        help=(
            "Scrape and append only; skip detail-page and LLM enrichment "
            "(fill later with --backfill)"
        ),
    )
    p_run.add_argument(
        "-S",
        "--sources",
        default=None,
        metavar="LIST",
        help=(
            "Comma-separated job-board names to search (overrides .dd-config.yaml). "
            "Use 'jobs run --list-sources' to see options."
        ),
    )
    p_run.add_argument(
        "--list-sources",
        action="store_true",
        help="List the available job-board names and exit",
    )
    add_global_flags(p_run)
    p_run.set_defaults(func=_run_scrape)

    p_status = nested.add_parser(
        "status", parents=parents, help="Show last-run metadata and job counts"
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
        "-s",
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


def _run_scrape(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from daily_driver.plugins.job_search.scraper import run as run_scrape
    from daily_driver.plugins.job_search.scraper import run_backfill
    from daily_driver.plugins.job_search.scraper.sources import SCRAPERS

    if getattr(args, "list_sources", False):
        for sid in sorted(SCRAPERS):
            print(sid)
        return 0

    sources_override: list[str] | None = None
    raw_sources = getattr(args, "sources", None)
    if raw_sources:
        sources_override = [s.strip() for s in raw_sources.split(",") if s.strip()]
        if not sources_override:
            Console.error("--sources parsed to an empty list (only commas/whitespace?)")
            return 2
        unknown = [s for s in sources_override if s not in SCRAPERS]
        if unknown:
            Console.error(
                f"unknown source(s): {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(SCRAPERS))}"
            )
            return 2

    plugins = workspace.config.plugins
    if plugins is None or plugins.job_search is None:
        Console.error(
            "no plugins.job_search found in .dd-config.yaml. "
            "See docs/configuration.md."
        )
        return 1

    plugin = plugins.job_search
    ai = workspace.config.ai
    # context.md, when present, rides every fit/notes enrichment prompt so the
    # fit score reflects the candidate's real background (see enrich_fit_and_notes).
    context_text = ""
    context_path = workspace.root / "context.md"
    if context_path.is_file():
        context_text = context_path.read_text(encoding="utf-8").strip()
    output_dir = workspace.output_dir
    csv_path = output_dir / "jobs.csv"
    ephemeral_dir = workspace.ephemeral_dir

    try:
        if args.backfill:
            run_backfill(
                plugin, csv_path, ephemeral_dir, ai=ai, context_text=context_text
            )
            return 0

        return run_scrape(
            plugin,
            output_dir,
            ephemeral_dir,
            ai=ai,
            context_text=context_text,
            dry_run=args.dry_run,
            no_enrich=args.no_enrich,
            sources_override=sources_override,
        )
    except KeyboardInterrupt:
        if args.backfill:
            # csv_io.backfill already printed the interrupt + backup-path message.
            return 130

        # SIGINT during a parallel run: pending sources were cancelled by the
        # orchestrator, but in-flight HTTP requests run to their `timeout`
        # before their worker threads exit. Exit 130 is the conventional
        # SIGINT status (128 + signal number).
        Console.warning(
            "\ninterrupted; cancelling pending sources "
            "(in-flight HTTP requests will finish first)."
        )
        return 130


def _run_prune(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from rich.console import Console as RichConsole
    from rich.table import Table

    from daily_driver.core.dates import parse_since
    from daily_driver.plugins.job_search.jobs_archive import (
        DEFAULT_PRUNE_STATUSES,
        prune,
    )

    try:
        cutoff = parse_since(args.older_than)
    except ValueError as exc:
        Console.error(str(exc))
        return 2

    if args.status:
        statuses = tuple(s.strip().lower() for s in args.status if s.strip())
    else:
        statuses = DEFAULT_PRUNE_STATUSES

    output_dir = workspace.output_dir
    csv_path = output_dir / "jobs.csv"

    candidates, archived = prune(
        csv_path,
        workspace.ephemeral_dir,
        cutoff=cutoff,
        statuses=statuses,
        dry_run=args.dry_run,
    )

    console = RichConsole(stderr=False)
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
    from rich.console import Console as RichConsole
    from rich.table import Table

    from daily_driver.plugins.job_search.scraper_status import build_status

    output_dir = workspace.output_dir
    status = build_status(output_dir)

    emit_json = getattr(args, "json", False)
    if emit_json:
        print(json.dumps({"schema": 1, "data": status}, indent=2))
        return 0

    console = RichConsole(stderr=False)

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
    from daily_driver.core.workspace import WorkspaceError

    if not hasattr(args, "func") or args.func is run:
        Console.error("usage: daily-driver jobs <action> ...")
        Console.error("actions: run, status, prune")
        return 2

    try:
        workspace = resolve_workspace(args)
    except WorkspaceError as exc:
        Console.error(str(exc))
        return 1

    try:
        return args.func(args, workspace)
    except Exception as exc:  # noqa: BLE001
        Console.error(str(exc))
        return 1
