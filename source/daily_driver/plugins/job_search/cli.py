"""jobs subcommand: job-search workflows (run, discover-boards, backfill, promote, status, prune)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.console import Console


def _int_at_least(minimum: int, flag: str) -> Callable[[str], int]:
    """Build an argparse ``type=`` callable parsing an integer >= ``minimum``.

    argparse turns the ArgumentTypeError into a clean exit 2.
    """

    def _parse(value: str) -> int:
        try:
            n = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
        if n < minimum:
            raise argparse.ArgumentTypeError(f"{flag} must be >= {minimum} (got {n})")
        return n

    return _parse


# A 0 limit would mean "spend nothing" (budget 0 = no calls) -- a pointless
# backfill better expressed by not running it -- and a negative limit is
# nonsense, so reject both at the parser.
_positive_limit = _int_at_least(1, "--limit")


def _cooldown_hours(value: str) -> int | str:
    """--cooldown-hours: a non-negative int (hours), or the literal ``missing``.

    ``0`` disables the cooldown (re-enrich every active row); ``missing``
    re-enriches only rows with no enrichment timestamp yet.
    """
    if value.strip().lower() == "missing":
        return "missing"
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "--cooldown-hours must be a non-negative integer or 'missing' "
            f"(got {value!r})"
        ) from None
    if n < 0:
        raise argparse.ArgumentTypeError(
            f"--cooldown-hours must be >= 0 or 'missing' (got {n})"
        )
    return n


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "jobs",
        parents=parents,
        help=(
            "Job search: run scrapes, discover boards, backfill enrichment, "
            "promote matches, inspect status, verify liveness, prune stale rows"
        ),
    )

    nested = parser.add_subparsers(dest="jobs_action", metavar="<action>")

    p_run = nested.add_parser(
        "run",
        parents=parents,
        help="Run the configured job-board search pipeline",
    )
    # --dry-run prints a human table to stdout; --json emits machine JSON to
    # stdout. Both own the stdout channel, and a dry-run writes no manifest, so
    # combining them would corrupt the JSON contract (table + bare object). Reject
    # the combination at the parser rather than silently producing junk.
    p_run_output = p_run.add_mutually_exclusive_group()
    p_run_output.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print results without writing to jobs.csv",
    )
    p_run_output.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help=(
            "After the run, emit the run manifest to stdout for scripting, "
            'wrapped as {"schema": 1, "data": <manifest>} (read e.g. '
            ".data.new_jobs). Suppresses the live progress block; diagnostics "
            "still go to stderr. Not combinable with --dry-run."
        ),
    )
    p_run.add_argument(
        "--no-enrich",
        action="store_true",
        help=(
            "Scrape and append only; skip detail-page and LLM enrichment "
            "(fill later with 'jobs backfill')"
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

    p_backfill = nested.add_parser(
        "backfill",
        parents=parents,
        help="Re-enrich empty fields in existing jobs.csv rows",
    )
    p_backfill.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help=(
            "Report what would be enriched (the fit/notes count) without making "
            "LLM calls or writing to jobs.csv"
        ),
    )
    p_backfill.add_argument(
        "--limit",
        type=_positive_limit,
        default=None,
        metavar="N",
        help=(
            "Cap LLM spend this run: bound the fit/notes budget at N "
            "(minimum 1; default: the configured cap)"
        ),
    )
    p_backfill.add_argument(
        "--force-update",
        action="store_true",
        default=False,
        help=(
            "Re-enrich every active row and OVERWRITE its Fit, Notes, and Remote "
            "(default: fill missing cells only). Still bounded by --limit and the "
            "--cooldown-hours cooldown"
        ),
    )
    p_backfill.add_argument(
        "--cooldown-hours",
        type=_cooldown_hours,
        default=None,
        metavar="N|missing",
        help=(
            "Only with --force-update (no effect otherwise): skip rows enriched "
            "within the last N hours, so an interrupted force-update resumes "
            "instead of restarting; 'missing' re-enriches only rows with no "
            "enrichment timestamp yet (default: config force_recook_cooldown_hours, "
            "normally 24; 0 disables)"
        ),
    )
    p_backfill.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help=(
            "Emit the completion summary as JSON. Suppresses the live progress "
            "block; diagnostics still go to stderr."
        ),
    )
    add_global_flags(p_backfill)
    p_backfill.set_defaults(func=_run_backfill)

    p_promote = nested.add_parser(
        "promote",
        parents=parents,
        help="Promote a jobs.csv row into a tracker `job` entry",
    )
    p_promote.add_argument(
        "selector",
        metavar="URL-OR-COMPANY",
        help=(
            "Job Link URL (exact match) or an unambiguous case-insensitive "
            "substring of Company"
        ),
    )
    p_promote.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print what would be created without writing to the tracker",
    )
    add_global_flags(p_promote)
    p_promote.set_defaults(func=_run_promote)

    p_discover = nested.add_parser(
        "discover-boards",
        parents=parents,
        help=(
            "Sweep the ATS slug universe (Greenhouse + Ashby + Lever) for boards "
            "listing in-scope roles; matched boards are cached for jobs run. "
            "Incremental after the first sweep (only never-probed slugs); the "
            "first sweep probes everything and takes tens of minutes"
        ),
    )
    p_discover.add_argument(
        "--full",
        action="store_true",
        default=False,
        help=(
            "Re-probe every known slug (except cached-dead ones) instead of "
            "only slugs never swept, so boards that stopped matching drop out "
            "of the cache. Takes tens of minutes, like a first sweep"
        ),
    )
    p_discover.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help=(
            "Emit the sweep summary as JSON. Suppresses the live progress "
            "block; diagnostics still go to stderr."
        ),
    )
    add_global_flags(p_discover)
    p_discover.set_defaults(func=_run_discover_boards)

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
        metavar="SPEC",
        default="1d",
        help=(
            "Prune rows last verified before SPEC "
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
            "Use --status applied --status interviewing to prune stale "
            "in-progress rows."
        ),
    )
    p_prune.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print prune candidates without writing to disk",
    )
    p_prune.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit the candidate/archived set as JSON instead of a Rich table",
    )
    add_global_flags(p_prune)
    p_prune.set_defaults(func=_run_prune)

    p_verify = nested.add_parser(
        "verify",
        parents=parents,
        help="URL-check stale untriaged rows from sources board-diff cannot cover",
    )
    p_verify.add_argument(
        "--reverify-days",
        type=_int_at_least(1, "--reverify-days"),
        default=None,
        metavar="N",
        help=(
            "Re-check rows whose liveness evidence is at least N days old "
            "(default: plugins.job_search.verify.reverify_days, 7)"
        ),
    )
    p_verify.add_argument(
        "--unverified-age-days",
        type=_int_at_least(1, "--unverified-age-days"),
        default=None,
        metavar="N",
        help=(
            "Close unverifiable rows (indeed, HN permalinks) found at least N "
            "days ago as age-unverified "
            "(default: plugins.job_search.verify.unverified_age_days, 30)"
        ),
    )
    p_verify.add_argument(
        "-S",
        "--sources",
        default=None,
        metavar="LIST",
        help=(
            "Comma-separated source ids to verify (e.g. linkedin,remoteok); "
            "same shape as jobs run -S"
        ),
    )
    p_verify.add_argument(
        "--limit",
        type=_positive_limit,
        default=None,
        metavar="N",
        help=(
            "Probe at most N URLs this run, stalest evidence first "
            "(age-unverified closures need no probe and are not capped)"
        ),
    )
    p_verify.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Probe URLs and report verdicts without writing to disk",
    )
    p_verify.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit the verify report as JSON instead of a Rich summary",
    )
    add_global_flags(p_verify)
    p_verify.set_defaults(func=_run_verify)

    parser.set_defaults(func=run)
    return parser


def _resolve_plugin_and_context(args, workspace):  # type: ignore[no-untyped-def]
    """Resolve (plugin, ai, context_text) or return an int exit code on error.

    context.md, when present, rides every fit/notes enrichment prompt so the fit
    score reflects the candidate's real background (see enrich_fit_and_notes).
    """
    plugins = workspace.config.plugins
    if plugins is None or plugins.job_search is None:
        Console.error(
            "no plugins.job_search found in .dd-config.yaml. "
            "See docs/configuration.md."
        )
        return 1
    context_text = ""
    context_path = workspace.root / "context.md"
    if context_path.is_file():
        context_text = context_path.read_text(encoding="utf-8").strip()
    return plugins.job_search, workspace.config.ai, context_text


def _emit_run_manifest(output_dir) -> None:  # type: ignore[no-untyped-def]
    """Print the run manifest (jobs-last-run.json) to stdout for `jobs run --json`.

    The runner writes the manifest on every exit that has a sink -- a clean
    completion (``interrupted=False``) or a Ctrl-C / SIGTERM / crash
    (``interrupted=True``). ``--json`` is mutually exclusive with ``--dry-run``
    (which writes no manifest), so under ``--json`` a manifest always exists; we
    read it back and re-emit it wrapped in the standard ``{"schema", "data"}``
    envelope (the on-disk manifest becomes ``data``) so stdout carries the
    machine-readable result while the runner's diagnostics stayed on stderr.

    If the manifest is unreadable (an I/O error or a corrupt body, not the
    dry-run case) emit the envelope with ``data: null`` so a scripted consumer
    still gets valid JSON, and warn on stderr naming the path so "unreadable" is
    distinguishable from "nothing to report".
    """
    manifest_path = output_dir / "jobs-last-run.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        Console.warning(f"could not read run manifest {manifest_path}: {exc}")
        Console.emit_json(None)
        return
    Console.emit_json(manifest)


def _run_scrape(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from daily_driver.plugins.job_search.scraper import run as run_scrape
    from daily_driver.plugins.job_search.scraper.enrichment._shared import (
        install_sigterm_handler,
        interrupted_by_sigterm,
        restore_sigterm_handler,
    )
    from daily_driver.plugins.job_search.scraper.sources import SCRAPERS

    if getattr(args, "list_sources", False):
        if getattr(args, "json", False):
            # --json owns stdout for jq; emit the source list as a JSON array
            # rather than bare lines so a --json consumer never gets plain text.
            Console.emit_json(sorted(SCRAPERS))
        else:
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

    resolved = _resolve_plugin_and_context(args, workspace)  # type: ignore[no-untyped-call]
    if isinstance(resolved, int):
        return resolved
    plugin, ai, context_text = resolved
    output_dir = workspace.output_dir
    ephemeral_dir = workspace.ephemeral_dir
    emit_json = getattr(args, "json", False)

    # A scheduled run is stopped with SIGTERM; install a run-scoped handler that
    # routes it through the same graceful drain/flush path as Ctrl-C (it raises
    # KeyboardInterrupt). Restored in finally so the handler never leaks past the
    # command.
    sigterm_prev = install_sigterm_handler()
    try:
        rc = run_scrape(
            plugin,
            output_dir,
            ephemeral_dir,
            ai=ai,
            context_text=context_text,
            dry_run=args.dry_run,
            no_enrich=args.no_enrich,
            sources_override=sources_override,
            suppress_live=emit_json,
        )
        if emit_json:
            _emit_run_manifest(output_dir)
        return rc
    except KeyboardInterrupt:
        # SIGTERM and SIGINT both unwind here; pick the conventional exit code
        # (143 = 128 + SIGTERM, 130 = 128 + SIGINT).
        sigterm = interrupted_by_sigterm()
        # Pending sources were cancelled by the orchestrator, but in-flight HTTP
        # requests run to their `timeout` before their worker threads exit.
        signal_name = "terminated" if sigterm else "interrupted"
        Console.warning(
            f"\n{signal_name}; cancelling pending sources "
            "(in-flight HTTP requests will finish first). "
            "Run jobs backfill to finish enrichment."
        )
        # The run() wrapper already wrote an interrupted=True manifest before
        # re-raising; re-emit it so a --json consumer gets the interrupted
        # manifest JSON on stdout rather than nothing. Exit code is unchanged.
        if emit_json:
            _emit_run_manifest(output_dir)
        return 143 if sigterm else 130
    except Exception:
        # A non-interrupt crash also has an interrupted=True manifest on disk
        # (the run() wrapper writes one on every exit); re-emit it so a --json
        # consumer gets the documented JSON on stdout instead of nothing, then
        # re-raise to the cli-level handler (stderr + exit 1).
        if emit_json:
            _emit_run_manifest(output_dir)
        raise
    finally:
        restore_sigterm_handler(sigterm_prev)


def _run_backfill(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from daily_driver.plugins.job_search.scraper import run_backfill
    from daily_driver.plugins.job_search.scraper.enrichment._shared import (
        install_sigterm_handler,
        interrupted_by_sigterm,
        restore_sigterm_handler,
    )

    resolved = _resolve_plugin_and_context(args, workspace)  # type: ignore[no-untyped-call]
    if isinstance(resolved, int):
        return resolved
    plugin, ai, context_text = resolved
    csv_path = workspace.output_dir / "jobs.csv"
    ephemeral_dir = workspace.ephemeral_dir
    emit_json = getattr(args, "json", False)

    sigterm_prev = install_sigterm_handler()
    try:
        summary = run_backfill(
            plugin,
            csv_path,
            ephemeral_dir,
            ai=ai,
            context_text=context_text,
            dry_run=args.dry_run,
            limit=args.limit,
            force=args.force_update,
            cooldown_hours=args.cooldown_hours,
            emit_json=emit_json,
        )
        if emit_json:
            Console.emit_json(summary)
        return 0
    except KeyboardInterrupt:
        # run_backfill already saved partial progress and printed the backup path.
        sigterm = interrupted_by_sigterm()
        return 143 if sigterm else 130
    finally:
        restore_sigterm_handler(sigterm_prev)


def _run_promote(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from rich.markup import escape

    from daily_driver.core.tracker import Tracker
    from daily_driver.plugins.job_search.promote import PromoteError, promote

    tracker = Tracker(workspace)
    jobs_csv = workspace.output_dir / "jobs.csv"

    try:
        result = promote(tracker, jobs_csv, args.selector, dry_run=args.dry_run)
    except PromoteError as exc:
        Console.error(str(exc))
        return 1

    # Console.print parses Rich markup, so escape values built from CSV cells
    # (a `[applied]` status or a `[bracketed]` title would otherwise be eaten as
    # a style tag and vanish from the line).
    title = escape(result.title)
    status = escape(result.status)

    if not result.created and result.already_promoted_id is not None:
        Console.success(f"already promoted as {result.already_promoted_id}: {title}")
        return 0

    # A blank or unrecognized row Status was silently recorded as the fallback;
    # surface it so the asserted state claim is visible.
    if result.status_fallback:
        if result.raw_status:
            Console.warning(
                f"row status {result.raw_status!r} not in the job lifecycle; "
                f"recorded as {result.status!r}"
            )
        else:
            Console.warning(f"row has no status; recorded as {result.status!r}")

    # No Link means promotion fell back to the weaker (company, role) dedup key;
    # flag it so the looser idempotency guarantee for this entry is visible.
    no_link_note = "" if result.has_link else " (row has no Link)"

    if args.dry_run:
        Console.info(f"would create job entry \\[{status}]: {title}{no_link_note}")
        return 0

    assert result.entry is not None  # created path always carries an entry
    Console.success(f"Promoted {result.entry.id} \\[{status}]: {title}{no_link_note}")
    return 0


def _run_prune(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
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
        # Normalization (case-fold, underscores -> hyphens) happens in _is_stale,
        # so pass the raw user values through and just drop blanks.
        statuses = tuple(s.strip() for s in args.status if s.strip())
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

    emit_json = getattr(args, "json", False)
    if emit_json:
        payload = {
            "dry_run": args.dry_run,
            "candidates": candidates,
            "archived": archived,
        }
        Console.emit_json(payload)
        return 0

    console = Console.get_user_console()
    if not candidates:
        console.print("[dim]No rows match prune criteria.[/dim]")
        return 0

    table = Table(
        title=f"Prune candidates ({'dry-run' if args.dry_run else 'archived'})",
        show_header=True,
    )
    table.add_column("Company")
    table.add_column("Status")
    table.add_column("Date Verified")
    table.add_column("Role")
    for row in candidates:
        table.add_row(
            row.get("Company", ""),
            row.get("Status", ""),
            row.get("Date Verified", "") or row.get("Date Found", ""),
            row.get("Role", ""),
        )
    console.print(table)
    if args.dry_run:
        console.print(f"[yellow]Dry-run: {len(candidates)} would be pruned.[/yellow]")
    else:
        console.print(f"[green]Archived {archived} rows to jobs.archive.csv.[/green]")
    return 0


def _run_verify(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from rich.table import Table

    from daily_driver.plugins.job_search.scraper.enrichment._shared import (
        install_sigterm_handler,
        interrupted_by_sigterm,
        restore_sigterm_handler,
    )
    from daily_driver.plugins.job_search.scraper.sources import SOURCE_CAPABILITIES
    from daily_driver.plugins.job_search.scraper.verify import verify_jobs

    sources: frozenset[str] | None = None
    raw_sources = getattr(args, "sources", None)
    if raw_sources:
        requested = [s.strip() for s in raw_sources.split(",") if s.strip()]
        if not requested:
            Console.error("--sources parsed to an empty list (only commas/whitespace?)")
            return 2
        # url-check sources get probed; verify="none" sources are still valid
        # targets here -- they are what the age-unverified fallback closes.
        verifiable = sorted(
            sid
            for sid, capability in SOURCE_CAPABILITIES.items()
            if capability.verify != "board-diff"
        )
        unknown = [s for s in requested if s not in verifiable]
        if unknown:
            Console.error(
                f"not verifiable by jobs verify: {', '.join(unknown)}. "
                f"Verifiable sources: {', '.join(verifiable)} "
                f"(board-backed sources are verified by jobs run's board-diff)"
            )
            return 2
        sources = frozenset(requested)

    resolved = _resolve_plugin_and_context(args, workspace)  # type: ignore[no-untyped-call]
    if isinstance(resolved, int):
        return resolved
    plugin, _ai, _context_text = resolved

    csv_path = workspace.output_dir / "jobs.csv"
    sigterm_prev = install_sigterm_handler()
    try:
        report = verify_jobs(
            csv_path,
            workspace.ephemeral_dir,
            plugin,
            reverify_days=args.reverify_days,
            unverified_age_days=args.unverified_age_days,
            sources=sources,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        # verify_jobs drains an interrupt inside its probe loop; landing here
        # means the interrupt hit outside it (e.g. waiting on the jobs lock),
        # so nothing was probed or written.
        Console.error("verify interrupted before it started; nothing written")
        return 143 if interrupted_by_sigterm() else 130
    finally:
        restore_sigterm_handler(sigterm_prev)

    interrupted_exit = 143 if interrupted_by_sigterm() else 130

    if getattr(args, "json", False):
        Console.emit_json(report.to_payload())
        return interrupted_exit if report.interrupted else 0

    console = Console.get_user_console()
    checked_total = sum(report.checked.values())
    if checked_total == 0 and not report.closed:
        if report.interrupted:
            console.print(
                "[yellow]Interrupted before the first probe finished; "
                "nothing was checked or written.[/yellow]"
            )
            return interrupted_exit
        console.print(
            f"[dim]No rows due for verification "
            f"({report.candidates} candidates before limit).[/dim]"
        )
        return 0

    if checked_total:
        per_source = ", ".join(
            f"{source} {count}" for source, count in sorted(report.checked.items())
        )
        console.print(f"[bold]Checked:[/bold] {checked_total} ({per_source})")
        console.print(f"  Live (Date Verified refreshed): {report.live}")
        unknown_total = sum(report.unknown.values())
        if unknown_total:
            reasons = ", ".join(
                f"{reason} {count}" for reason, count in sorted(report.unknown.items())
            )
            console.print(f"  Unknown (never closes): {unknown_total} ({reasons})")

    if report.closed:
        table = Table(
            title=f"Closed ({'dry-run' if args.dry_run else 'written'})",
            show_header=True,
        )
        table.add_column("Company")
        table.add_column("Role")
        table.add_column("Source")
        table.add_column("Evidence")
        for entry in report.closed:
            table.add_row(
                entry["company"], entry["role"], entry["source"], entry["reason"]
            )
        console.print(table)
    console.print(f"  Closed: {len(report.closed)}")
    aged = sum(1 for entry in report.closed if entry["reason"] == "age-unverified")
    if aged:
        console.print(
            f"  [dim]{aged} closed by age: no URL reflects these postings' "
            f"state, and their last liveness evidence is past the "
            f"unverified-age threshold.[/dim]"
        )

    for source in report.suspect_sources:
        console.print(
            f"[red]All checked {source} rows read closed -- detector suspect; "
            f"no {source} closures applied. Check the site by hand.[/red]"
        )
    if report.discarded_closures:
        for entry in report.discarded_closures:
            console.print(f"  [dim]discarded ({entry['reason']}): {entry['url']}[/dim]")
    if report.interrupted:
        console.print(
            "[yellow]Interrupted mid-probe; outcomes gathered so far were "
            "applied.[/yellow]"
        )
    if args.dry_run:
        console.print("[yellow]Dry-run: nothing written.[/yellow]")
    return interrupted_exit if report.interrupted else 0


def _run_discover_boards(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from daily_driver.core.progress import RunProgress
    from daily_driver.plugins.job_search.scraper.discovery import (
        DiscoveryError,
        run_discovery,
    )
    from daily_driver.plugins.job_search.scraper.enrichment._shared import (
        install_sigterm_handler,
        interrupted_by_sigterm,
        restore_sigterm_handler,
    )

    resolved = _resolve_plugin_and_context(args, workspace)  # type: ignore[no-untyped-call]
    if isinstance(resolved, int):
        return resolved
    plugin, _ai, _context_text = resolved
    state_dir = workspace.ephemeral_dir
    emit_json = getattr(args, "json", False)

    tty = Console.live_progress_enabled(suppress=emit_json)
    sigterm_prev = install_sigterm_handler()
    try:
        with RunProgress(
            Console.get_log_console(), tty=tty, title="Board discovery sweep"
        ) as rp:
            group = rp.group("Sweeping slug universe")

            def _progress(platform: str, total: int):  # type: ignore[no-untyped-def]
                phase = group.phase(platform, total=total)
                return phase.advance

            summary = run_discovery(
                plugin,
                state_dir,
                full=args.full,
                progress=_progress,
            )
            group.done()
    except DiscoveryError as exc:
        Console.error(str(exc))
        return 1
    except TimeoutError:
        Console.error("another discovery sweep holds the lock; wait for it to finish")
        return 1
    except KeyboardInterrupt:
        sigterm = interrupted_by_sigterm()
        signal_name = "terminated" if sigterm else "interrupted"
        # An interrupted --full sweep must resume WITH --full: a plain rerun
        # skips everything already swept, including the stale entries the
        # full re-probe was meant to refresh.
        Console.warning(
            f"\n{signal_name}; recorded probes are saved -- rerun "
            "jobs discover-boards with the same flags to continue the sweep."
        )
        return 143 if sigterm else 130
    finally:
        restore_sigterm_handler(sigterm_prev)

    if emit_json:
        Console.emit_json(summary)
        return 0

    for platform, stats in summary["platforms"].items():
        source_note = (
            " (cached slug list)" if stats["universe_source"] == "cache" else ""
        )
        skipped = stats["universe"] - stats["candidates"]
        skip_note = f"; {skipped} already swept or dead" if skipped > 0 else ""
        Console.info(
            f"{platform}: {stats['swept']} probed of {stats['candidates']} "
            f"candidates ({stats['universe']} known slugs{source_note}"
            f"{skip_note}) -> {stats['matched_new']} newly matched "
            f"({stats['matched_total']} total in cache), "
            f"{stats['dead_new']} newly dead, "
            f"{stats['transient']} transient failures (retry next sweep)"
        )
    Console.info(
        "Matched boards are scraped by jobs run alongside your configured "
        "*_boards pins (exclude_boards drops noisy ones). Use --full to "
        "re-probe already-swept boards."
    )
    return 0


def _run_status(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from rich.table import Table

    from daily_driver.plugins.job_search.scraper_status import build_status

    output_dir = workspace.output_dir
    status = build_status(output_dir, workspace.ephemeral_dir)

    emit_json = getattr(args, "json", False)
    if emit_json:
        Console.emit_json(status)
        return 0

    console = Console.get_user_console()

    last_run = status["last_run"]
    if last_run is None:
        console.print("[yellow]No scraper run recorded yet.[/yellow]")
    else:
        console.print(f"[bold]Last run:[/bold] {last_run.get('started_at', '?')}")
        console.print(f"  New jobs:       {last_run.get('new_jobs', '?')}")
        sources_ok = last_run.get("sources_ok") or []
        sources_failed = last_run.get("sources_failed") or []
        sources_degraded = last_run.get("sources_degraded") or []
        console.print(f"  Sources OK:     {', '.join(sources_ok) or 'none'}")
        if sources_failed:
            console.print(f"  [red]Sources failed:[/red] {', '.join(sources_failed)}")
        if sources_degraded:
            console.print(
                f"  [yellow]Sources degraded:[/yellow] "
                f"{', '.join(sources_degraded)}"
            )
        if last_run.get("interrupted"):
            # The last run was cut short (Ctrl-C / SIGTERM / crash); point the
            # user at the resume path so the half-enriched rows get finished.
            phase = last_run.get("phase_reached", "enrichment")
            console.print(
                f"  [yellow]Last run interrupted during {phase} -- "
                "run jobs backfill to finish enrichment.[/yellow]"
            )

    last_verify = status.get("last_verify")
    if last_verify is not None:
        checked = sum((last_verify.get("checked") or {}).values())
        closed = len(last_verify.get("closed") or [])
        started = (last_verify.get("started_at") or "?")[:10]
        console.print(
            f"[bold]Last verify:[/bold] {started} -- "
            f"{checked} checked, {last_verify.get('live', 0)} live, {closed} closed"
        )
        suspects = last_verify.get("suspect_sources") or []
        if suspects:
            # A tripped detector-rot breaker must not hide on the dashboard:
            # its closures were discarded pending a by-hand check of the site.
            console.print(
                f"  [red]Suspect detector(s), closures discarded: "
                f"{', '.join(suspects)}[/red]"
            )
        if last_verify.get("interrupted"):
            console.print("  [yellow]Last verify was interrupted mid-probe.[/yellow]")

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
        # Cumulative across runs, not a last-run stat: rows no fit pass has
        # reached yet. Growth means inflow is outpacing the fit budget. This
        # count includes rows awaiting a description re-scrape, which neither
        # a bigger budget nor jobs backfill can score (the run summary's
        # "awaiting description" bucket).
        console.print(
            f"Unscored backlog (found/pending, no fit score yet): "
            f"{status['unscored_backlog']}"
        )
    else:
        console.print("[dim]No jobs.csv found.[/dim]")

    discovery = status.get("discovery") or {}
    if discovery:
        console.print("[bold]Discovered boards:[/bold]")
        for platform, stats in sorted(discovery.items()):
            last = stats.get("last_swept") or "never"
            # ISO stamp -> date for the human line; --json keeps the full stamp.
            last_day = last[:10] if last != "never" else last
            console.print(
                f"  {platform}: {stats.get('boards_matched', 0)} boards matched "
                f"({stats.get('slugs_swept', 0)} slugs swept, "
                f"last sweep {last_day})"
            )

    return 0


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.workspace import WorkspaceError

    if not hasattr(args, "func") or args.func is run:
        Console.error("usage: daily-driver jobs <action> ...")
        Console.error(
            "actions: run, discover-boards, backfill, promote, status, verify, prune"
        )
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
