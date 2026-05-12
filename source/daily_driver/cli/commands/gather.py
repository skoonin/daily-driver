"""gather subcommand: read external state (calendar, git).

Thin CLI wrapper over ``daily_driver.gathers.*`` that emits plain-text or
JSON suitable for piping into a Claude prompt.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from daily_driver.cli._common import add_global_flags
from daily_driver.core.clock import today
from daily_driver.core.console import Console
from daily_driver.core.workspace import Workspace, WorkspaceError
from daily_driver.gathers import calendar as gather_calendar
from daily_driver.gathers import git as gather_git


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "gather",
        parents=parents,
        help="Pull data from calendar or git",
    )
    nested = parser.add_subparsers(dest="gather_what", metavar="<what>")

    p_cal = nested.add_parser(
        "calendar", parents=parents, help="Read macOS Calendar events"
    )
    p_cal.add_argument("--since", default=None, help="ISO date (default: today)")
    p_cal.add_argument("--until", default=None, help="ISO date (default: tomorrow)")
    p_cal.add_argument("-j", "--json", action="store_true", help="Emit JSON")
    add_global_flags(p_cal)
    p_cal.set_defaults(func=_run_calendar)

    p_git = nested.add_parser(
        "git", parents=parents, help="Read recent git commits from a repo"
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
    p_git.add_argument("-j", "--json", action="store_true", help="Emit JSON")
    add_global_flags(p_git)
    p_git.set_defaults(func=_run_git)

    parser.set_defaults(func=run)
    return parser


def _parse_date(raw: str | None, default: date) -> date:
    """Resolve a gather --since/--until value.

    Accepts the full grammar of ``daily_driver.core.dates.parse_since``
    (today/yesterday/tomorrow, week/month/quarter/year, Nd|Nw|Nm|Ny,
    YYYY-MM-DD). ``None`` returns the supplied default.
    """
    if raw is None:
        return default
    from daily_driver.core.dates import parse_since

    return parse_since(raw)


def _as_dt(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _run_calendar(args: argparse.Namespace, workspace: Workspace) -> int:
    del workspace
    try:
        since_d = _parse_date(args.since, today())
        until_d = _parse_date(args.until, today() + timedelta(days=1))
    except ValueError as exc:
        Console.error(f"invalid date: {exc}")
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
    try:
        since_d = _parse_date(args.since, today() - timedelta(days=1))
        until_d = _parse_date(args.until, today() + timedelta(days=1))
    except ValueError as exc:
        Console.error(f"invalid date: {exc}")
        return 2

    since_dt = _as_dt(since_d)
    until_dt = _as_dt(until_d)

    if args.repo is not None:
        repos: list[Path] = [args.repo]
    else:
        configured = list(workspace.config.gather.git.search_paths)
        if configured:
            from daily_driver.core.git_discovery import discover_repos

            expanded = [Path(p).expanduser() for p in configured]
            repos = discover_repos(expanded)
            if not repos:
                Console.info(
                    "(no git repos discovered under configured search_paths: "
                    f"{', '.join(str(p) for p in expanded)})"
                )
                return 0
        else:
            repos = [Path.cwd()]

    commits: list[gather_git.GitCommit] = []
    for repo in repos:
        commits.extend(gather_git.gather_commits(repo, since_dt, until_dt))
    commits.sort(key=lambda c: c.timestamp)

    if args.json:
        payload = {
            "commits": [c.model_dump(mode="json") for c in commits],
            "count": len(commits),
            "repos": [str(r) for r in repos],
        }
        print(json.dumps({"schema": 1, "data": payload}, indent=2))
    else:
        if not commits:
            scanned = ", ".join(str(r) for r in repos)
            print(f"(no commits in {scanned})")
            return 0
        for c in commits:
            print(f"{c.sha}  {c.timestamp.strftime('%Y-%m-%d %H:%M')}  {c.subject}")
    return 0


def run(args: argparse.Namespace) -> int:
    if not hasattr(args, "func") or args.func is run:
        Console.error("usage: daily-driver gather <what> ...")
        Console.error("what: calendar, git")
        return 2

    override = getattr(args, "workspace", None)
    try:
        workspace = Workspace.discover_or_fail(
            override=Path(override) if override else None
        )
    except WorkspaceError as exc:
        Console.error(str(exc))
        return 1

    return args.func(args, workspace)
