"""voice-update subcommand: update voice-profile.md from writing samples via claude."""

from __future__ import annotations

import argparse
from pathlib import Path

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.cli.commands._claude_session import (
    default_session_name,
    handle_launch_exception,
    launch_headless,
    require_claude_available,
)
from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.core.voice import (
    VoiceUpdateError,
    apply_update,
    build_prompt,
    collect_source_files,
)
from daily_driver.integrations import clipboard

log = get_logger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "voice-update",
        parents=parents,
        help="Update voice-profile.md from writing samples (uses Claude)",
    )
    parser.add_argument(
        "--from",
        dest="sources",
        metavar="PATH",
        nargs="+",
        required=True,
        help="Source files or directories (.md/.txt). Directories are recursed.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--append",
        dest="mode",
        action="store_const",
        const="append",
        default="append",
        help="Append new observations to the existing profile (default).",
    )
    mode_group.add_argument(
        "--replace",
        dest="mode",
        action="store_const",
        const="replace",
        help="Replace the profile; backs up the original as voice-profile.md.bak.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be written without modifying any files.",
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        default=False,
        help="Skip copying the updated profile to the clipboard.",
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="Custom name for this Claude session (defaults to a timestamped name).",
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
        help="Seconds to wait for Claude before giving up (default: 180).",
    )
    add_global_flags(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    source_paths = [Path(p) for p in args.sources]
    mode = args.mode or "append"

    try:
        source_files = collect_source_files(source_paths)
    except VoiceUpdateError as exc:
        Console.error(str(exc))
        return 1

    try:
        workspace = resolve_workspace(args)
        require_claude_available()
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)

    profile_path = workspace.output_dir / "voice-profile.md"
    current_profile = (
        profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    )

    prompt = build_prompt(source_files, current_profile=current_profile, mode=mode)

    if args.dry_run:
        Console.info(f"dry-run: would invoke claude with {len(prompt)} char prompt")
        Console.info(f"dry-run: would write to {profile_path}")
        return 0

    try:
        new_content = launch_headless(
            slash_command=prompt,
            workspace=workspace,
            session_name=default_session_name("voice-update", args.session_name),
            model=args.model,
            timeout=args.timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)

    # Normalize to a single trailing newline for consistent file formatting.
    normalized = new_content.strip() + "\n"

    try:
        # Lock a sentinel, not the data file itself: apply_update does an
        # os.replace, which would sever a lock held on the original inode and
        # break mutual exclusion between concurrent runs.
        with file_lock(profile_path.with_suffix(".md.lock")):
            apply_update(profile_path, new_content=normalized, mode=mode)
    except VoiceUpdateError as exc:
        Console.error(str(exc))
        return 1

    log.debug("voice-profile.md updated at %s", profile_path)

    if not args.no_clipboard and clipboard.available():
        try:
            clipboard.copy(normalized.strip())
        except Exception as exc:  # noqa: BLE001
            log.debug("clipboard unavailable: %s", exc)

    return 0
