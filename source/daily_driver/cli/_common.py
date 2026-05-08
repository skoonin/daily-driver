"""Shared CLI plumbing: global flag parser + post-parse configuration.

The argparse `parents=` machinery lets every leaf subcommand accept the same
global flags (`-v/-q/--no-color/--workspace`) that the top-level parser
accepts. Using `default=argparse.SUPPRESS` on each global flag is essential:
it prevents the subparser from overwriting a value the top-level parser
already set on the shared `Namespace`. Reads must therefore go through
`getattr(args, "name", default)`, not `args.name`.
"""

from __future__ import annotations

import argparse
import os

import daily_driver.core.logging as dd_logging


def build_global_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    verbosity = parent.add_mutually_exclusive_group()
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
    parent.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable Rich color/formatting output.",
    )
    parent.add_argument(
        "--workspace",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Path to daily-driver workspace root.",
    )
    return parent


GLOBAL_PARSER = build_global_parser()


def dry_run_skip_claude(
    args: argparse.Namespace, *, action: str = "claude invocation"
) -> bool:
    """Return True (and print a notice) when args.dry_run is set.

    Single source of truth so every command with `--dry-run` can short-circuit
    claude calls the same way. The notice goes to stderr so JSON output paths
    aren't polluted.
    """
    if not getattr(args, "dry_run", False):
        return False
    import sys

    print(f"dry-run: skipping {action}", file=sys.stderr)
    return True


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
