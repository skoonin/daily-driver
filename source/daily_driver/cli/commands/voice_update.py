"""voice-update subcommand: update voice-profile.md from writing samples."""

from __future__ import annotations

import argparse
from pathlib import Path

from daily_driver.cli._common import add_global_flags, int_at_least, resolve_workspace
from daily_driver.cli.commands._ai_task import run_ai_task
from daily_driver.cli.commands._session import handle_launch_exception
from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.core.providers import provider_for
from daily_driver.core.voice import (
    VoiceUpdateError,
    apply_update,
    build_prompt,
    collect_source_files,
    merge_observations,
    parse_observations,
)
from daily_driver.integrations import clipboard
from daily_driver.tasks import VOICE_UPDATE

log = get_logger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "voice-update",
        parents=parents,
        help="Update voice-profile.md from writing samples (uses AI)",
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
        help=(
            "Append new observations to the existing profile (default). "
            "Backs up the original as voice-profile.md.bak."
        ),
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
        help=(
            "Validate sources and print the target path without calling the "
            "model or writing (does not preview the generated profile)."
        ),
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        default=False,
        help="Skip copying the updated profile to the clipboard.",
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
            "Seconds to wait for an answer (default: the provider's timeout, "
            "else 180)."
        ),
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
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)

    provider = provider_for(workspace.config, VOICE_UPDATE)

    profile_path = workspace.root / "voice-profile.md"
    # Read once for prompt context only; the authoritative merge base is re-read
    # under the lock after the model call (L1), so a concurrent write landing
    # during the up-to-timeout model call is not clobbered.
    profile_for_prompt = (
        profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    )

    prompt = build_prompt(source_files, current_profile=profile_for_prompt, mode=mode)

    if args.dry_run:
        Console.info(
            f"dry-run: would invoke {provider.name} with {len(prompt)} char prompt"
        )
        Console.info(f"dry-run: would write to {profile_path}")
        return 0

    new_content = run_ai_task(
        "voice-update",
        provider,
        VOICE_UPDATE,
        prompt,
        workspace=workspace.root,
        model=args.model,
        timeout=args.timeout,
    )
    if new_content is None:
        return 1

    # Read-modify-write under the lock so the merge base and the write are
    # atomic against concurrent runs (L1). The sentinel lives under
    # ephemeral_dir rather than the user-visible output dir (L2), and is a
    # sentinel — not the data file itself — because apply_update does an
    # os.replace that would sever a lock held on the original inode.
    normalized = ""
    try:
        with file_lock(workspace.ephemeral_dir / "voice-profile.lock"):
            current_profile = (
                profile_path.read_text(encoding="utf-8")
                if profile_path.exists()
                else ""
            )

            # Append mode: the model returns only new observations; merge them
            # into the existing document (it is never regenerated). Replace mode
            # takes the model's full rewritten profile verbatim.
            if mode == "append":
                observations = parse_observations(new_content)
                merged = merge_observations(current_profile, observations)
                if not observations or merged.strip() == current_profile.strip():
                    Console.info("no new observations found; voice profile unchanged")
                    return 0
                content = merged
            else:
                content = new_content

            # Normalize to a single trailing newline for consistent formatting.
            normalized = content.strip() + "\n"
            apply_update(
                profile_path,
                new_content=normalized,
                mode=mode,
                current_profile=current_profile,
            )
    except VoiceUpdateError as exc:
        Console.error(str(exc))
        return 1
    except (OSError, TimeoutError) as exc:
        # Backup (shutil.copy2), lock acquisition, or the atomic write can fail
        # with OSError/TimeoutError. The original profile is left intact on every
        # such path (backup precedes the overwrite; the write is atomic), so
        # surface a clear message instead of a raw traceback.
        Console.error(f"could not write voice profile (original left unchanged): {exc}")
        return 1

    log.debug("voice-profile.md updated at %s", profile_path)

    if not args.no_clipboard and clipboard.available():
        try:
            clipboard.copy(normalized.strip())
        except Exception as exc:  # noqa: BLE001
            log.debug("clipboard unavailable: %s", exc)

    return 0
