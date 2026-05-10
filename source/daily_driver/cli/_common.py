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
from typing import NoReturn

import daily_driver.core.logging as dd_logging


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
        action="store_true",
        default=argparse.SUPPRESS,
        help="Enable debug-level logging.",
    )
    verbosity.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Suppress all output below WARNING.",
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


def configure(args: argparse.Namespace) -> None:
    """Apply verbosity + NO_COLOR side effects from parsed global flags."""
    if getattr(args, "verbose", False):
        dd_logging.configure("verbose")
    elif getattr(args, "quiet", False):
        dd_logging.configure("quiet")
    else:
        dd_logging.configure("normal")

    if getattr(args, "no_color", False):
        os.environ["NO_COLOR"] = "1"
