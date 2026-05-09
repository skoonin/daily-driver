"""read subcommand: print workspace text files (context, voice-profile, plan).

Module is named ``read_`` to avoid shadowing Python's builtin ``read``.
The argparse subcommand itself is ``read``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from daily_driver.cli._common import add_global_flags
from daily_driver.cli.commands._utils import resolve_date
from daily_driver.core.workspace import Workspace, WorkspaceError


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "read",
        parents=parents,
        help="Print workspace text files (context, voice-profile, plan)",
    )
    nested = parser.add_subparsers(dest="read_what", metavar="<what>")

    p_context = nested.add_parser("context", parents=parents, help="Print context.md")
    add_global_flags(p_context)
    p_context.set_defaults(func=_run_context)

    p_voice = nested.add_parser(
        "voice-profile",
        parents=parents,
        help="Print voice-profile.md (prints a marker if missing or empty)",
    )
    add_global_flags(p_voice)
    p_voice.set_defaults(func=_run_voice_profile)

    p_plan = nested.add_parser("plan", parents=parents, help="Print a daily plan file")
    p_plan.add_argument(
        "--date",
        default=None,
        help="ISO date (YYYY-MM-DD); defaults to today",
    )
    p_plan.add_argument(
        "--frontmatter",
        action="store_true",
        help="Only emit YAML frontmatter",
    )
    add_global_flags(p_plan)
    p_plan.set_defaults(func=_run_plan)

    parser.set_defaults(func=run)
    return parser


def _plan_path(workspace: Workspace, when: date) -> Path:
    return (
        workspace.output_dir
        / f"{when.year:04d}"
        / f"{when.month:02d}"
        / f"{when.isoformat()}-plan.md"
    )


def _extract_frontmatter(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith("---"):
        return ""
    out: list[str] = []
    closed = False
    for line in lines[1:]:
        if line.startswith("---"):
            closed = True
            break
        out.append(line)
    if not closed:
        return ""
    return "".join(out)


def _run_context(args: argparse.Namespace, workspace: Workspace) -> int:
    context = workspace.output_dir / "context.md"
    if not context.is_file():
        print(f"error: context.md not found at {context}", file=sys.stderr)
        print(
            "       Run `daily-driver init` or copy the template and fill it in.",
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(context.read_text(encoding="utf-8"))
    return 0


def _run_voice_profile(args: argparse.Namespace, workspace: Workspace) -> int:
    # Empty-state contract matches `gather`: exit 0, print a stable
    # `(no voice profile ...)` marker to stdout. Skills branch on the marker
    # without breaking bash chains in shipped slash commands.
    profile = workspace.output_dir / "voice-profile.md"
    if not profile.is_file():
        print(f"(no voice profile found at {profile})")
        return 0
    text = profile.read_text(encoding="utf-8")
    if not text.strip():
        print(f"(no voice profile found at {profile} — file is empty)")
        return 0
    sys.stdout.write(text)
    return 0


def _run_plan(args: argparse.Namespace, workspace: Workspace) -> int:
    try:
        when = resolve_date(args.date)
    except ValueError:
        print(f"error: invalid --date: {args.date}", file=sys.stderr)
        return 2
    plan = _plan_path(workspace, when)
    if not plan.is_file():
        # Same empty-state contract as `gather` and `read voice-profile`:
        # exit 0 + stdout marker, so shipped slash-command bash chains
        # don't abort when day-start hasn't run yet.
        print(f"(no plan found at {plan} — run /day-start first)")
        return 0
    text = plan.read_text(encoding="utf-8")
    if args.frontmatter:
        sys.stdout.write(_extract_frontmatter(text))
    else:
        sys.stdout.write(text)
    return 0


def run(args: argparse.Namespace) -> int:
    if not hasattr(args, "func") or args.func is run:
        print("usage: daily-driver read <what> ...", file=sys.stderr)
        print("what: context, voice-profile, plan", file=sys.stderr)
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
