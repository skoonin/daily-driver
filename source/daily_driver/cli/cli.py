"""CLI entry point for daily-driver.

Builds the argparse tree and dispatches subcommands. Global flags
(`-v/-q/--no-color/--workspace`) are registered via
`cli._common.add_global_flags(parser)` on the top-level parser and inside
each leaf's `add_parser` (called AFTER local args so they render at the
bottom of `--help`). This makes `daily-driver -v tracker list` and
`daily-driver tracker list -v` both parse.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from typing import Protocol, cast

import daily_driver
from daily_driver.cli._common import (
    HelpfulArgumentParser,
    add_global_flags,
    configure,
)

# Table of (subcommand-name, dotted-module-path) in registration order.
# All entries must resolve at import time — ImportError is a packaging bug,
# not a graceful-degradation case.
_COMMANDS = [
    ("init", "daily_driver.cli.commands.init"),
    ("doctor", "daily_driver.cli.commands.doctor"),
    ("tracker", "daily_driver.cli.commands.tracker"),
    ("status", "daily_driver.cli.commands.status"),
    ("focus", "daily_driver.cli.commands.focus"),
    ("jobs", "daily_driver.cli.commands.jobs"),
    ("paths", "daily_driver.cli.commands.paths"),
    ("gather", "daily_driver.cli.commands.gather"),
    ("day-start", "daily_driver.cli.commands.day_start"),
    ("day-end", "daily_driver.cli.commands.day_end"),
    ("check-in", "daily_driver.cli.commands.check_in"),
    ("summary", "daily_driver.cli.commands.summary"),
    ("scheduler", "daily_driver.cli.commands.scheduler"),
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
    parser = HelpfulArgumentParser(
        prog="daily-driver",
        description=(
            "Daily Driver — ADHD-friendly daily planning, focus, and "
            "task tracking. Drives professional work, job search, and errands."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"daily-driver {daily_driver.__version__}",
    )

    # parser_class propagates HelpfulArgumentParser to every nested
    # subparser, so `daily-driver focus on` (etc.) also points at --help
    # on parse errors.
    subparsers = parser.add_subparsers(
        dest="cmd", metavar="<command>", parser_class=HelpfulArgumentParser
    )

    # Subcommands invoked by the shipped slash commands but not useful as
    # day-to-day user-facing CLI verbs. Hidden from `daily-driver --help`
    # listing while remaining fully callable. Keep this set in sync with
    # source/daily_driver/commands/daily-driver/*.md.
    _HIDDEN_FROM_TOP_HELP = {"paths"}

    # Deferred imports keep --version / --help fast and avoid circular imports
    # at module load time.  All command modules are shipped in-package; an
    # ImportError here means a packaging defect, not a graceful-degradation
    # scenario — let it propagate so the user sees a clear traceback.
    _cmd_map: dict[str, _CommandModule] = {}
    for cmd_name, module_path in _COMMANDS:
        module = cast(_CommandModule, importlib.import_module(module_path))
        module.add_parser(subparsers, [])  # type: ignore[arg-type]
        _cmd_map[cmd_name] = module

    # Strip hidden subcommands from the help listing. Standard argparse
    # workaround — `help=argparse.SUPPRESS` on the subparser itself renders
    # the literal "==SUPPRESS==" text under Python 3.11 (bpo-22848). The
    # entries remain in subparsers.choices so parsing/dispatch is unaffected.
    subparsers._choices_actions = [
        a for a in subparsers._choices_actions if a.dest not in _HIDDEN_FROM_TOP_HELP
    ]

    # Register globals on the top-level parser AFTER subparsers so they render
    # at the bottom of `daily-driver --help`.
    add_global_flags(parser)

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
