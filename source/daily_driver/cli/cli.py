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
import sys
from typing import Protocol, cast

import daily_driver
from daily_driver.cli._common import (
    HelpfulArgumentParser,
    add_global_flags,
    configure,
)
from daily_driver.core.console import Console
from daily_driver.core.logging import get_logger
from daily_driver.plugins import PLUGINS

# Table of (subcommand-name, dotted-module-path) in registration order.
# Module-path entries must resolve when dispatched — ImportError is a
# packaging bug, not a graceful-degradation case. Consumed by help.py to
# walk the command set; keep the 2-tuple shape it unpacks.
#
# Plugin commands (PLUGINS) are appended after the core commands, so they
# list last in `--help`.
_CORE_COMMANDS = [
    ("init", "daily_driver.cli.commands.init"),
    ("doctor", "daily_driver.cli.commands.doctor"),
    ("tracker", "daily_driver.cli.commands.tracker"),
    ("status", "daily_driver.cli.commands.status"),
    ("focus", "daily_driver.cli.commands.focus"),
    ("paths", "daily_driver.cli.commands.paths"),
    ("gather", "daily_driver.cli.commands.gather"),
    ("day-start", "daily_driver.cli.commands.day_start"),
    ("day-end", "daily_driver.cli.commands.day_end"),
    ("check-in", "daily_driver.cli.commands.check_in"),
    ("summary", "daily_driver.cli.commands.summary"),
    ("scheduler", "daily_driver.cli.commands.scheduler"),
    ("calendar", "daily_driver.cli.commands.calendar"),
    ("voice-update", "daily_driver.cli.commands.voice_update"),
    ("help", "daily_driver.cli.commands.help"),
]

_PLUGIN_COMMANDS = [(p.command_name, p.command_module) for p in PLUGINS]

_COMMANDS = _CORE_COMMANDS + _PLUGIN_COMMANDS

# Top-level `daily-driver --help` summaries, mirroring each module's own
# add_parser `help=`. Stored alongside (not in) _COMMANDS so the listing
# renders without importing any command module — importing all of them costs
# ~150ms+ via Jinja2/pydantic/requests transitive deps. Drift from a module's
# real `help=` only affects the top-level listing, never dispatch.
_TOP_HELP = {
    "init": "Create a new daily-driver workspace",
    "doctor": "Check installation and workspace health",
    "tracker": (
        "Manage tracker entries (add, update, delete, prune, show, list, "
        "follow-ups, stats)"
    ),
    "status": "Show workspace status and tracker summary",
    "focus": "Manage focus mode (on / off / status)",
    "paths": "Print resolved workspace paths (output, state, daily plan/notes)",
    "gather": "Pull data from calendar or git",
    "day-start": "Interactive morning planning session (runs /daily-driver:day-start via claude)",
    "day-end": "Interactive end-of-day review session (runs /daily-driver:day-end via claude)",
    "check-in": "Interactive mid-day check-in session (runs /daily-driver:check-in via claude)",
    "summary": "Generate a period summary using Claude (or --json for raw data)",
    "scheduler": "Install, uninstall, or check the macOS background scheduler",
    "calendar": "Write today's plan time blocks to a local macOS Calendar",
    "voice-update": "Update voice-profile.md from writing samples (uses Claude)",
    "help": "Show reference: subcommands and discoverable values",
}
_TOP_HELP.update({p.command_name: p.command_help for p in PLUGINS})

_COMMAND_MODULES = {name: module_path for name, module_path in _COMMANDS}


class _CommandModule(Protocol):
    def add_parser(
        self,
        subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
        parents: list[argparse.ArgumentParser],
    ) -> argparse.ArgumentParser: ...

    def run(self, args: argparse.Namespace) -> int: ...


# Global flags that consume the following token as a value; skip both when
# scanning argv for the subcommand name so e.g. `-w jobs` (a workspace path
# literally named "jobs") doesn't get mistaken for the jobs subcommand.
_VALUE_FLAGS = {"-w", "--workspace"}


def _select_command(argv: list[str]) -> str | None:
    """Return the first token in argv that names a known subcommand.

    Stops at the first positional so flags meant for the subcommand aren't
    scanned. Returns None when no subcommand token is present (bare invocation,
    `--version`, `--help`, or a parse error to be surfaced by argparse).
    """
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _VALUE_FLAGS:
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token if token in _COMMAND_MODULES else None
    return None


def app(argv: list[str] | None = None) -> int:
    """Build parser, parse argv, dispatch. Returns exit code.

    argv=None reads sys.argv[1:] via parse_args().
    Bare `daily-driver` (no subcommand) prints help and returns 2.
    `daily-driver --version` prints version string and returns 0.
    """
    # allow_abbrev=False: lazy dispatch scans argv for the subcommand before
    # argparse runs, so an abbreviated global flag (e.g. `--work` for
    # `--workspace`) would not be recognized as value-consuming and its value
    # would be misread as the subcommand. Disabling abbreviation keeps the scan
    # and argparse in agreement.
    parser = HelpfulArgumentParser(
        prog="daily-driver",
        allow_abbrev=False,
        description=(
            "Daily Driver — Personal daily planning, focus, and "
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
    # source/daily_driver/resources/slash_commands/daily-driver/*.md.
    _HIDDEN_FROM_TOP_HELP = {"paths"}

    # Importing every command module at parser-build time drags Jinja2,
    # pydantic, and requests into the process for `--version` / `--help`.
    # Instead, scan argv for the invoked subcommand and build only its real
    # parser; every other command gets a help-only stub so the top-level
    # listing and choice validation stay complete without the imports.
    # ImportError on dispatch is a packaging defect, not a graceful-
    # degradation scenario — let it propagate so the user sees a traceback.
    selected = _select_command(list(argv) if argv is not None else sys.argv[1:])
    for cmd_name, module_path in _COMMANDS:
        if cmd_name == selected:
            module = cast(_CommandModule, importlib.import_module(module_path))
            module.add_parser(subparsers, [])  # type: ignore[arg-type]
        else:
            subparsers.add_parser(cmd_name, help=_TOP_HELP[cmd_name], add_help=False)

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

    if args.cmd not in _COMMAND_MODULES:
        parser.error(f"unknown subcommand: {args.cmd!r}")

    cmd_module = cast(
        _CommandModule, importlib.import_module(_COMMAND_MODULES[args.cmd])
    )
    try:
        return cmd_module.run(args)
    except Exception as exc:  # noqa: BLE001
        logger = get_logger("cli")
        _write_error_log(args, exc)
        if (getattr(args, "verbose", 0) or 0) >= 1:
            logger.exception("unhandled error in %r", args.cmd)
        else:
            Console.error(f"{type(exc).__name__}: {exc}")
            cause = exc.__cause__
            if cause is not None:
                Console.error(f"  caused by: {type(cause).__name__}: {cause}")
        return 1


def _write_error_log(args: argparse.Namespace, exc: BaseException) -> None:
    """Best-effort full-traceback dump to <state_dir>/logs/cli-error.log.

    Sole-maintainer debugging: capture the traceback even when the user did
    not pass -v. Resolving the workspace can itself fail (no workspace in
    CWD, unreadable config); a failed log write must never change the
    user-facing error or exit code, so everything here is swallowed.
    """
    import traceback

    from daily_driver.cli._common import resolve_workspace
    from daily_driver.core.workspace import WorkspaceError

    try:
        try:
            state_dir = resolve_workspace(args).state_dir
        except WorkspaceError:
            return
        log_dir = state_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "cli-error.log").write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
