"""summary subcommand: period summary via headless claude or --json bundle."""

from __future__ import annotations

import argparse
import json
import logging

from daily_driver.cli._common import add_global_flags
from daily_driver.cli.commands._claude_session import (
    default_session_name,
    handle_launch_exception,
    launch_headless,
    require_claude_available,
    resolve_workspace,
)
from daily_driver.core.summary import build_json_bundle, parse_range, render_prompt
from daily_driver.integrations import clipboard

log = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "summary",
        parents=parents,
        help="Generate a period summary (headless via claude, or --json bundle)",
    )
    parser.add_argument(
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
        "--json",
        action="store_true",
        dest="emit_json",
        help="Emit a JSON bundle instead of invoking claude",
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Skip copying the result to the clipboard",
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="Override the auto-generated claude session display name",
    )
    parser.add_argument(
        "--agent",
        default="work-planner",
        help="Agent to load (default: work-planner)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model alias or name (e.g., 'sonnet', 'opus')",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Seconds to wait for claude before failing (default: 180)",
    )
    add_global_flags(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        range_start, range_end = parse_range(args.range_spec)
    except ValueError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
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

    # Headless claude path
    try:
        workspace = resolve_workspace(args)
        require_claude_available()
        prompt = render_prompt(
            range_start=range_start,
            range_end=range_end,
            detail=args.detail,
            match=match,
        )
        output = launch_headless(
            slash_command=prompt,
            workspace=workspace,
            session_name=default_session_name("summary", args.session_name),
            agent=args.agent,
            model=args.model,
            timeout=args.timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)

    text = output.strip()
    print(text)
    if not args.no_clipboard and clipboard.available():
        try:
            clipboard.copy(text)
        except Exception as exc:  # noqa: BLE001
            log.debug("clipboard unavailable: %s", exc)
    return 0
