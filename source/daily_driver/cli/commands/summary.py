"""summary subcommand: period summary via the summary provider, or --json bundle."""

from __future__ import annotations

import argparse
import json

from daily_driver.cli._common import add_global_flags, int_at_least, resolve_workspace
from daily_driver.cli.commands._session import handle_launch_exception
from daily_driver.core.console import Console
from daily_driver.core.logging import get_logger

log = get_logger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "summary",
        parents=parents,
        help="Generate a period summary using AI (or --json for raw data)",
    )
    parser.add_argument(
        "-r",
        "--range",
        required=True,
        metavar="SPEC",
        dest="range_spec",
        help=(
            "Date range: today | yesterday | week | month | "
            "YYYY-MM-DD | YYYY-MM-DD:YYYY-MM-DD"
        ),
    )
    parser.add_argument(
        "--detail",
        choices=["low", "med", "high"],
        default="med",
        help="Verbosity level of the summary (default: med)",
    )
    parser.add_argument(
        "--match",
        action="append",
        metavar="KW",
        default=[],
        help="Keyword filter; may be repeated (e.g. --match python --match sre)",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        dest="emit_json",
        help="Print raw JSON data instead of running the AI",
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Skip copying the result to the clipboard",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model for this run; overrides the provider's model.",
    )
    parser.add_argument(
        "--timeout",
        type=int_at_least(1, "--timeout"),
        default=None,
        help=(
            "Seconds to wait for the summary (default: the provider's timeout, "
            "else 900)."
        ),
    )
    add_global_flags(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    # deferred: core.summary pulls in jinja2, and the provider layer pulls in
    # requests; neither is needed at parser build.
    from daily_driver.cli.commands._ai_task import run_ai_task
    from daily_driver.core.providers import provider_for
    from daily_driver.core.summary import (
        build_json_bundle,
        parse_range,
        render_prompt,
        summary_files,
    )
    from daily_driver.core.tracker import Tracker
    from daily_driver.integrations import clipboard
    from daily_driver.tasks import SUMMARY

    try:
        range_start, range_end = parse_range(args.range_spec)
    except ValueError as exc:
        Console.error(str(exc))
        return 1

    match = args.match or []

    if args.emit_json:
        bundle = build_json_bundle(
            range_start=range_start,
            range_end=range_end,
            detail=args.detail,
            match=match,
        )
        print(json.dumps(bundle))
        return 0

    try:
        workspace = resolve_workspace(args)
        provider = provider_for(workspace.config, SUMMARY)
        files = summary_files(
            workspace, range_start, range_end, tracker=Tracker(workspace).path
        )
        if not files:
            # Nothing on disk to summarize; an AI call would only say so.
            Console.info(
                f"no daily notes, plans or tracker for {range_start} to {range_end}"
            )
            return 0
        prompt = render_prompt(
            range_start=range_start,
            range_end=range_end,
            detail=args.detail,
            match=match,
            files=files,
        )
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)

    output = run_ai_task(
        "summary",
        provider,
        SUMMARY,
        prompt,
        workspace=workspace.root,
        model=args.model,
        timeout=args.timeout,
    )
    if output is None:
        return 1

    text = output.strip()
    print(text)
    if not args.no_clipboard and clipboard.available():
        try:
            clipboard.copy(text)
        except Exception as exc:  # noqa: BLE001
            log.debug("clipboard unavailable: %s", exc)
    return 0
