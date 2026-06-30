"""focus subcommand: manage focus mode via a lock file in ephemeral_dir."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
from pathlib import Path

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.workspace import Workspace


def _parse_duration(value: str) -> int:
    """Parse a duration string to total minutes.

    Accepts: 30m, 2h, 1h30m, or bare integer (minutes).
    Raises argparse.ArgumentTypeError on invalid input.
    """
    value = value.strip()
    if re.fullmatch(r"\d+", value):
        return int(value)
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", value)
    if match and (match.group(1) or match.group(2)):
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        total = hours * 60 + minutes
        if total > 0:
            return total
    raise argparse.ArgumentTypeError(
        f"invalid duration {value!r}: use 30m, 2h, 1h30m, or bare minutes"
    )


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "focus",
        parents=parents,
        help="Manage focus mode (on / off / status)",
    )

    nested = parser.add_subparsers(dest="focus_action", metavar="<action>")

    p_on = nested.add_parser("on", parents=parents, help="Start focus mode")
    p_on.add_argument(
        "--for",
        dest="duration",
        default=None,
        metavar="DURATION",
        type=_parse_duration,
        help=(
            "Duration: 30m, 2h, 1h30m, or bare minutes "
            "(default: focus.default_duration in .dd-config.yaml, fallback 25m)"
        ),
    )
    p_on.add_argument(
        "--reason",
        default=None,
        metavar="TEXT",
        help="Optional reason",
    )
    add_global_flags(p_on)
    p_on.set_defaults(func=_run_on)

    p_off = nested.add_parser("off", parents=parents, help="End focus mode")
    add_global_flags(p_off)
    p_off.set_defaults(func=_run_off)

    p_status = nested.add_parser(
        "status", parents=parents, help="Show focus mode state"
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

    parser.set_defaults(func=run)
    return parser


def _lock_path(workspace: Workspace) -> Path:
    return workspace.ephemeral_dir / "focus.lock"


def _run_on(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from daily_driver.core.clock import now

    console = Console.get_user_console()

    duration_minutes = args.duration
    if duration_minutes is None:
        configured = workspace.config.focus.default_duration
        try:
            duration_minutes = _parse_duration(configured)
        except argparse.ArgumentTypeError as exc:
            Console.error(f"invalid focus.default_duration in .dd-config.yaml: {exc}")
            Console.error("Run: daily-driver focus on --help")
            return 2
        args.duration = duration_minutes

    start = now()
    end = start + datetime.timedelta(minutes=duration_minutes)
    end_epoch = int(end.timestamp())

    payload = {
        "start_iso": start.isoformat(),
        "end_iso": end.isoformat(),
        "end_epoch": end_epoch,
        "reason": args.reason,
    }

    lock = _lock_path(workspace)
    lock.parent.mkdir(parents=True, exist_ok=True)
    # focus.lock is intentionally both lock and payload (lock-as-data). Write
    # the payload atomically (temp + os.replace) so a crash mid-write can never
    # leave a truncated JSON body that `focus status` would fail to parse.
    with file_lock(lock, shared=False):
        tmp = lock.with_name(lock.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, lock)

    end_hm = end.strftime("%H:%M")
    msg = f"Focus mode enabled until {end_hm} ({args.duration}m)"
    if args.reason:
        msg += f" — reason: {args.reason}"
    console.print(msg)
    return 0


def _run_off(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    console = Console.get_user_console()
    lock = _lock_path(workspace)
    if lock.exists():
        with file_lock(lock, shared=False):
            lock.unlink(missing_ok=True)
        console.print("Focus mode disabled")
    else:
        console.print("not in focus mode")
    return 0


def _run_status(args: argparse.Namespace, workspace) -> int:  # type: ignore[no-untyped-def]
    from daily_driver.core.clock import now

    console = Console.get_user_console()
    lock = _lock_path(workspace)
    emit_json = getattr(args, "json", False)

    if not lock.exists():
        if emit_json:
            payload = {"enabled": False, "started_at": None, "reason": None}
            print(json.dumps({"schema": 1, "data": payload}, indent=2))
        else:
            console.print("not in focus mode")
        return 0

    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        Console.error(f"reading focus lock: {exc}")
        return 1

    current = now()
    end_epoch = data.get("end_epoch", 0)
    if int(current.timestamp()) >= end_epoch:
        with file_lock(lock, shared=False):
            lock.unlink(missing_ok=True)
        if emit_json:
            payload = {"enabled": False, "started_at": None, "reason": None}
            print(json.dumps({"schema": 1, "data": payload}, indent=2))
        else:
            console.print("Focus mode expired")
        return 0

    end_dt = datetime.datetime.fromtimestamp(end_epoch, tz=current.tzinfo)
    remaining = max(0, int((end_dt - current).total_seconds() // 60))
    end_hm = end_dt.strftime("%H:%M")
    reason = data.get("reason")

    if emit_json:
        payload = {
            "enabled": True,
            "started_at": data.get("start_iso"),
            "reason": reason,
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2))
    else:
        console.print("Focus mode: Active")
        console.print(f"Until: {end_hm} ({remaining}m remaining)")
        if reason:
            console.print(f"Reason: {reason}")
    return 0


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.workspace import WorkspaceError

    if not hasattr(args, "func") or args.func is run:
        Console.error("usage: daily-driver focus <action> ...")
        Console.error("actions: on, off, status")
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
