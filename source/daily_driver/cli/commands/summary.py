"""summary subcommand: period summary via headless claude or --json bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.cli.commands._claude_session import (
    default_session_name,
    handle_launch_exception,
    launch_headless,
    require_claude_available,
)
from daily_driver.core.config import load as load_config
from daily_driver.core.console import Console
from daily_driver.core.logging import get_logger

log = get_logger(__name__)


def _resolve_summary_route(
    workspace_root: Path, *, cli_model: str | None = None
) -> tuple[Literal["claude", "ollama"], str | None]:
    """Resolve the (provider, model) route for the summary task.

    Missing config file → the claude default (empty AIConfig). Parse /
    validation errors propagate so the user sees the real cause; silent
    fallback to claude on a typo'd `ai.summary.provider: ollama` would route
    the request through the wrong backend without warning. Resolving here
    (not just `ai.summary.provider`) fixes the gap-10 bug: the claude path now
    honors `ai.summary.model` because provider and model are walked together.
    """
    from daily_driver.core.config_models import AIConfig
    from daily_driver.integrations import ai_provider

    cfg_path = workspace_root / ".dd-config.yaml"
    ai = load_config(cfg_path).ai if cfg_path.is_file() else AIConfig()
    return ai_provider.resolve_route(ai, task="summary", cli_model=cli_model)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "summary",
        parents=parents,
        help="Generate a period summary using Claude (or --json for raw data)",
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
        help="Print raw JSON data instead of running Claude",
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Skip copying the result to the clipboard",
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="Custom name for this Claude session (defaults to a timestamped name)",
    )
    parser.add_argument(
        "--agent",
        default="work-planner",
        help="Claude agent to load (default: work-planner)",
    )
    parser.add_argument(
        "--model",
        default=None,
        choices=["sonnet", "opus", "haiku"],
        help="Claude model to use.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Seconds to wait for Claude before giving up (default: 180)",
    )
    add_global_flags(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    # deferred: core.summary pulls in requests (~47ms) and ai_provider /
    # clipboard are only needed on the dispatch path, not at parser build.
    from daily_driver.core.summary import (
        build_json_bundle,
        parse_range,
        render_prompt,
    )
    from daily_driver.integrations import ai_provider, clipboard
    from daily_driver.integrations.ai_provider import (
        AIInvocationError,
        AITimeoutError,
    )

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

    # Headless path: dispatch through the provider layer so users can route
    # summary to ollama via `ai.summary.provider: ollama`. Claude remains the
    # default and keeps its workspace / agent / session context.
    try:
        workspace = resolve_workspace(args)
        prompt = render_prompt(
            range_start=range_start,
            range_end=range_end,
            detail=args.detail,
            match=match,
        )
        provider, model = _resolve_summary_route(workspace.root, cli_model=args.model)
        if provider == "claude":
            require_claude_available()
            output = launch_headless(
                slash_command=prompt,
                workspace=workspace,
                session_name=default_session_name("summary", args.session_name),
                agent=args.agent,
                model=model,
                timeout=args.timeout,
            )
        else:
            # Ollama (and any future non-claude provider) has no workspace /
            # agent / session concept — send the prompt as-is.
            output = ai_provider.invoke_for(
                prompt,
                provider=provider,
                model=model,
                ai=workspace.config.ai,
                timeout=args.timeout,
            )
    except AITimeoutError as exc:
        Console.error(f"{exc.provider} summary timed out after {exc.timeout_seconds}s")
        return 1
    except AIInvocationError as exc:
        msg = f"{exc.provider} summary failed: {exc}"
        tail = (exc.stderr or exc.stdout or "").strip()[-200:]
        if tail:
            msg = f"{msg}\n{tail}"
        Console.error(msg)
        return 1
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
