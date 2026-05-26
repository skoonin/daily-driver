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
