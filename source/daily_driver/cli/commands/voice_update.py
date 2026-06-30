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
    merge_observations,
    parse_observations,
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
        help=(
            "Seconds to wait for the Claude route before giving up (default: "
            "180); the ollama route is bounded by ai.ollama.timeout instead."
        ),
    )
    add_global_flags(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    from daily_driver.integrations import ai_provider
    from daily_driver.integrations.ai_provider import (
        AIInvocationError,
        AITimeoutError,
    )

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

    # voice-update is a routable task: claude (the default) keeps its workspace
    # / session / agent context via launch_headless; any other provider has no
    # such concept and goes through the plain dispatch layer.
    provider, model = ai_provider.resolve_route(
        workspace.config.ai, task="voice_update", cli_model=args.model
    )

    profile_path = workspace.output_dir / "voice-profile.md"
    # Read once for prompt context only; the authoritative merge base is re-read
    # under the lock after the model call (L1), so a concurrent write landing
    # during the up-to-timeout model call is not clobbered.
    profile_for_prompt = (
        profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    )

    prompt = build_prompt(source_files, current_profile=profile_for_prompt, mode=mode)

    if args.dry_run:
        Console.info(f"dry-run: would invoke {provider} with {len(prompt)} char prompt")
        Console.info(f"dry-run: would write to {profile_path}")
        return 0

    try:
        if provider == "claude":
            require_claude_available()
            new_content = launch_headless(
                slash_command=prompt,
                workspace=workspace,
                session_name=default_session_name("voice-update", args.session_name),
                model=model,
                timeout=args.timeout,
            )
        else:
            # Pass timeout=None so the ollama route is governed by
            # ai.ollama.timeout; --timeout bounds only the claude route above.
            new_content = ai_provider.invoke_for(
                prompt,
                provider=provider,
                model=model,
                ai=workspace.config.ai,
                timeout=None,
            )
    except AITimeoutError as exc:
        Console.error(
            f"{exc.provider} voice-update timed out after {exc.timeout_seconds}s"
        )
        return 1
    except AIInvocationError as exc:
        msg = f"{exc.provider} voice-update failed: {exc}"
        tail = (exc.stderr or exc.stdout or "").strip()[-200:]
        if tail:
            msg = f"{msg}\n{tail}"
        Console.error(msg)
        return 1
    except Exception as exc:  # noqa: BLE001
        return handle_launch_exception(exc)

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
