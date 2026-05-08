"""CLI entry point for daily-driver.

Builds the argparse tree and dispatches subcommands. Global flags
(`-v/-q/--no-color/--workspace`) live on `cli._common.GLOBAL_PARSER` and are
applied to both the top-level parser and every leaf via `parents=[...]`,
so they work in either position: `daily-driver -v tracker list` and
`daily-driver tracker list -v` both parse.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from typing import Protocol, cast

import daily_driver
from daily_driver.cli._common import GLOBAL_PARSER, configure

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


def app(argv: list[str] | None = None) -> int:
    """Build parser, parse argv, dispatch. Returns exit code.

    argv=None reads sys.argv[1:] via parse_args().
    Bare `daily-driver` (no subcommand) prints help and returns 2.
    `daily-driver --version` prints version string and returns 0.
    """
    parser = argparse.ArgumentParser(
        prog="daily-driver",
        description="Daily Driver — job search planning and accountability CLI.",
        parents=[GLOBAL_PARSER],
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
        module.add_parser(subparsers, [GLOBAL_PARSER])
        _cmd_map[cmd_name] = module

    # parse_args() raises SystemExit for --version, --help, or bad flags.
    args = parser.parse_args(argv)

    configure(args)

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
        if getattr(args, "verbose", False):
            logger.exception("unhandled error in %r", args.cmd)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
