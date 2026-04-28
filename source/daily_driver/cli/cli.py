"""CLI entry point for daily-driver.

Builds the argparse tree, defers command imports so --version and --help
work even when Stream B/C command modules are not yet present.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from typing import Protocol, cast

import daily_driver
import daily_driver.core.logging as dd_logging

# Table of (subcommand-name, dotted-module-path) in registration order.
# All entries must resolve at import time — ImportError is a packaging bug,
# not a graceful-degradation case.
_COMMANDS = [
    ("init", "daily_driver.cli.commands.init"),
    ("doctor", "daily_driver.cli.commands.doctor"),
    ("tracker", "daily_driver.cli.commands.tracker"),
    ("status", "daily_driver.cli.commands.status"),
    ("focus", "daily_driver.cli.commands.focus"),
    ("scrape-jobs", "daily_driver.cli.commands.scrape_jobs"),
    ("paths", "daily_driver.cli.commands.paths"),
    ("read", "daily_driver.cli.commands.read_"),
    ("ensure-daily-dir", "daily_driver.cli.commands.ensure_daily_dir"),
    ("gather", "daily_driver.cli.commands.gather"),
    ("day-start", "daily_driver.cli.commands.day_start"),
    ("day-end", "daily_driver.cli.commands.day_end"),
    ("check-in", "daily_driver.cli.commands.check_in"),
    ("summary", "daily_driver.cli.commands.summary"),
    ("install-scheduler", "daily_driver.cli.commands.install_scheduler"),
    ("uninstall-scheduler", "daily_driver.cli.commands.uninstall_scheduler"),
    ("voice-update", "daily_driver.cli.commands.voice_update"),
]


class _CommandModule(Protocol):
    def add_parser(
        self,
        subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
        parents: list[argparse.ArgumentParser],
    ) -> argparse.ArgumentParser: ...

    def run(self, args: argparse.Namespace) -> int: ...


def _build_parent_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    verbosity = parent.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable debug-level logging.",
    )
    verbosity.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress all output below WARNING.",
    )
    parent.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable Rich color/formatting output.",
    )
    parent.add_argument(
        "--workspace",
        metavar="PATH",
        default=None,
        help="Path to daily-driver workspace root.",
    )
    return parent


def app(argv: list[str] | None = None) -> int:
    """Build parser, parse argv, dispatch. Returns exit code.

    argv=None reads sys.argv[1:] via parse_args().
    Bare `daily-driver` (no subcommand) prints help and returns 2.
    `daily-driver --version` prints version string and returns 0.
    """
    parent = _build_parent_parser()

    parser = argparse.ArgumentParser(
        prog="daily-driver",
        description="Daily Driver — job search planning and accountability CLI.",
        parents=[parent],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"daily-driver {daily_driver.__version__}",
    )

    subparsers = parser.add_subparsers(dest="cmd", metavar="<command>")

    # Deferred imports keep --version / --help fast and avoid circular imports
    # at module load time.  All command modules are shipped in-package; an
    # ImportError here means a packaging defect, not a graceful-degradation
    # scenario — let it propagate so the user sees a clear traceback.
    _cmd_map: dict[str, _CommandModule] = {}
    for cmd_name, module_path in _COMMANDS:
        module = cast(_CommandModule, importlib.import_module(module_path))
        module.add_parser(subparsers, [])
        _cmd_map[cmd_name] = module

    # parse_args() raises SystemExit for --version, --help, or bad flags.
    args = parser.parse_args(argv)

    # Apply verbosity before dispatching so commands inherit the level.
    if args.verbose:
        dd_logging.configure("verbose")
    elif args.quiet:
        dd_logging.configure("quiet")
    else:
        dd_logging.configure("normal")

    if args.no_color:
        os.environ["NO_COLOR"] = "1"

    if args.cmd is None:
        parser.print_help(sys.stderr)
        return 2

    if args.cmd not in _cmd_map:
        parser.error(f"unknown subcommand: {args.cmd!r}")

    cmd_module = _cmd_map[args.cmd]
    try:
        return cmd_module.run(args)
    except Exception as exc:  # noqa: BLE001
        logger = logging.getLogger("daily_driver")
        if args.verbose:
            logger.exception("unhandled error in %r", args.cmd)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
