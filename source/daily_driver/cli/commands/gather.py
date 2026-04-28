"""gather subcommand: read external state (calendar, git, sessions, notes).

Thin CLI wrapper over ``daily_driver.gathers.*`` that emits plain-text or
JSON suitable for piping into a Claude prompt.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from daily_driver.core.clock import today
from daily_driver.core.workspace import Workspace, WorkspaceError
from daily_driver.gathers import calendar as gather_calendar
from daily_driver.gathers import git as gather_git
from daily_driver.gathers import notes as gather_notes
from daily_driver.gathers import sessions as gather_sessions


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "gather",
        parents=parents,
        help="Read external state (calendar, git, sessions, notes)",
    )
    nested = parser.add_subparsers(dest="gather_what", metavar="<what>")

    p_cal = nested.add_parser("calendar", parents=[], help="Read macOS Calendar events")
    p_cal.add_argument("--since", default=None, help="ISO date (default: today)")
    p_cal.add_argument("--until", default=None, help="ISO date (default: tomorrow)")
    p_cal.add_argument("--json", action="store_true", help="Emit JSON")
    p_cal.set_defaults(func=_run_calendar)

    p_git = nested.add_parser(
        "git", parents=[], help="Read recent git commits from a repo"
    )
    p_git.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current working directory)",
    )
    p_git.add_argument(
        "--since",
        default=None,
        help="ISO date (default: 24h before now)",
    )
    p_git.add_argument("--until", default=None, help="ISO date (default: now)")
    p_git.add_argument("--json", action="store_true", help="Emit JSON")
    p_git.set_defaults(func=_run_git)

    p_sess = nested.add_parser("sessions", parents=[], help="Read Claude Code sessions")
    p_sess.add_argument("--since", default=None, help="ISO date (default: today)")
    p_sess.add_argument("--until", default=None, help="ISO date (default: tomorrow)")
    p_sess.add_argument("--json", action="store_true", help="Emit JSON")
    p_sess.set_defaults(func=_run_sessions)

    p_notes = nested.add_parser(
        "notes", parents=[], help="List note file paths in a date range"
    )
    p_notes.add_argument("--since", default=None, help="ISO date (default: 7d ago)")
    p_notes.add_argument("--until", default=None, help="ISO date (default: tomorrow)")
    p_notes.add_argument("--json", action="store_true", help="Emit JSON")
    p_notes.set_defaults(func=_run_notes)

    parser.set_defaults(func=run)
    return parser


def _parse_date(raw: str | None, default: date) -> date:
    if raw is None:
        return default
    return date.fromisoformat(raw)


def _as_dt(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _run_calendar(args: argparse.Namespace, workspace: Workspace) -> int:
    del workspace
    try:
        since_d = _parse_date(args.since, today())
        until_d = _parse_date(args.until, today() + timedelta(days=1))
    except ValueError as exc:
        print(f"error: invalid date: {exc}", file=sys.stderr)
        return 2

    events = gather_calendar.gather_events(_as_dt(since_d), _as_dt(until_d))

    if args.json:
        payload = {
            "events": [e.model_dump(mode="json") for e in events],
            "count": len(events),
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2))
    else:
        if not events:
            print("(no calendar events)")
            return 0
        for ev in events:
            end = ev.end.strftime("%H:%M") if ev.end else "--:--"
            line = f"{ev.start.strftime('%Y-%m-%d %H:%M')}-{end}  {ev.title}"
            if ev.location:
                line += f"  @ {ev.location}"
            print(line)
    return 0


def _run_git(args: argparse.Namespace, workspace: Workspace) -> int:
    del workspace
    try:
        since_d = _parse_date(args.since, today() - timedelta(days=1))
        until_d = _parse_date(args.until, today() + timedelta(days=1))
    except ValueError as exc:
        print(f"error: invalid date: {exc}", file=sys.stderr)
        return 2

    repo = args.repo or Path.cwd()
    commits = gather_git.gather_commits(repo, _as_dt(since_d), _as_dt(until_d))

    if args.json:
        payload = {
            "commits": [c.model_dump(mode="json") for c in commits],
            "count": len(commits),
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2))
    else:
        if not commits:
            print(f"(no commits in {repo})")
            return 0
        for c in commits:
            print(f"{c.sha}  {c.timestamp.strftime('%Y-%m-%d %H:%M')}  {c.subject}")
    return 0


def _run_sessions(args: argparse.Namespace, workspace: Workspace) -> int:
    del workspace
    try:
        since_d = _parse_date(args.since, today())
        until_d = _parse_date(args.until, today() + timedelta(days=1))
    except ValueError as exc:
        print(f"error: invalid date: {exc}", file=sys.stderr)
        return 2

    sessions = gather_sessions.gather_sessions(_as_dt(since_d), _as_dt(until_d))

    if args.json:
        payload = {
            "sessions": [s.model_dump(mode="json") for s in sessions],
            "count": len(sessions),
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2))
    else:
        if not sessions:
            print("(no Claude sessions in window)")
            return 0
        for s in sessions:
            print(
                f"{s.started_at.strftime('%Y-%m-%d %H:%M')}  "
                f"{s.session_id[:8]}  msgs={s.message_count}  "
                f"cwd={s.cwd or '-'}"
            )
    return 0


def _run_notes(args: argparse.Namespace, workspace: Workspace) -> int:
    try:
        since_d = _parse_date(args.since, today() - timedelta(days=7))
        until_d = _parse_date(args.until, today() + timedelta(days=1))
    except ValueError as exc:
        print(f"error: invalid date: {exc}", file=sys.stderr)
        return 2

    paths = gather_notes.gather_note_paths(
        workspace.output_dir, _as_dt(since_d), _as_dt(until_d)
    )
    if getattr(args, "json", False):
        payload = {"paths": [str(p) for p in paths], "count": len(paths)}
        print(json.dumps({"schema": 1, "data": payload}, indent=2))
        return 0
    if not paths:
        print("(no notes in window)")
        return 0
    for p in paths:
        print(p)
    return 0


def run(args: argparse.Namespace) -> int:
    if not hasattr(args, "func") or args.func is run:
        print("usage: daily-driver gather <what> ...", file=sys.stderr)
        print("what: calendar, git, sessions, notes", file=sys.stderr)
        return 2

    override = getattr(args, "workspace", None)
    try:
        workspace = Workspace.discover_or_fail(
            override=Path(override) if override else None
        )
    except WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return args.func(args, workspace)
