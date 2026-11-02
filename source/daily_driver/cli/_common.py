"""Shared CLI plumbing: global-flag registration + post-parse configuration.

Global flags (`-v/-q/--no-color/--workspace`) are registered via
`add_global_flags(parser)` called AFTER each leaf's local arguments so they
render at the bottom of `--help` under a "global options" group. Every
flag uses `default=argparse.SUPPRESS` so a value set at one level (top,
group, or leaf parser) isn't silently clobbered by a later level's default
on the shared `Namespace`. Reads must use `getattr(args, name, default)`,
not `args.name`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import daily_driver.core.logging as dd_logging
from daily_driver.core.console import Console
from daily_driver.core.workspace import Workspace


class HelpfulArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that points users at --help on parse errors.

    Standard argparse prints usage + error and exits 2. We append
    `Run: {prog} --help` so the pointer to detailed help is one line away,
    instead of forcing the user to read the usage line and infer it.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.stderr.write(f"\nRun: {self.prog} --help\n")
        sys.exit(2)


def int_at_least(minimum: int, flag: str) -> Callable[[str], int]:
    """Build an argparse ``type=`` callable parsing an integer >= ``minimum``.

    argparse turns the ArgumentTypeError into a clean exit 2.
    """

    def _parse(value: str) -> int:
        try:
            n = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
        if n < minimum:
            raise argparse.ArgumentTypeError(f"{flag} must be >= {minimum} (got {n})")
        return n

    return _parse


def add_global_flags(parser: argparse.ArgumentParser) -> None:
    """Register the global flags on parser. Call AFTER local args."""
    g = parser.add_argument_group("global options")
    verbosity = g.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=argparse.SUPPRESS,
        help="Increase logging detail (-v: INFO, -vv: DEBUG).",
    )
    verbosity.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Show errors only.",
    )
    g.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable Rich color/formatting output.",
    )
    g.add_argument(
        "-w",
        "--workspace",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Path to your daily-driver workspace directory.",
    )


def add_session_args(parser: argparse.ArgumentParser) -> None:
    """Register the session flags shared by the interactive launchers.

    day-start, day-end, check-in, and the generic launcher factory all expose
    the same `--session-name` / `--agent` / `--model` trio; this keeps their
    definitions in one place. Call BEFORE add_global_flags so these render
    above the global-options group.
    """
    parser.add_argument(
        "--session-name",
        default=None,
        help="Custom name for this session (defaults to a timestamped name)",
    )
    parser.add_argument(
        "--agent",
        default="work-planner",
        help="Agent to load, where the provider supports one (default: work-planner)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model for this session; overrides the provider's model.",
    )


def resolve_workspace(args: argparse.Namespace) -> Workspace:
    """Discover the workspace from the global `--workspace` flag or CWD.

    Raises `WorkspaceError` when no workspace is found; callers translate it
    to a console error + exit code.
    """
    override = getattr(args, "workspace", None)
    return Workspace.discover_or_fail(override=Path(override) if override else None)


def configure(args: argparse.Namespace) -> None:
    """Apply verbosity + NO_COLOR side effects from parsed global flags."""
    verbose_count = getattr(args, "verbose", 0) or 0
    quiet = getattr(args, "quiet", False)
    no_color = getattr(args, "no_color", False)

    if quiet:
        dd_logging.configure("quiet")
    elif verbose_count >= 2:
        dd_logging.configure("debug")
    elif verbose_count >= 1:
        dd_logging.configure("verbose")
    else:
        dd_logging.configure("normal")

    Console.setup_for_user(
        quiet=quiet,
        verbose=verbose_count >= 2,
        no_color=no_color,
    )

    if no_color:
        os.environ["NO_COLOR"] = "1"
