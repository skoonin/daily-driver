"""day-start subcommand: interactive morning planning session via `claude`."""

from __future__ import annotations

import argparse

from daily_driver.cli.commands._claude_session import register_interactive_launcher


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    return register_interactive_launcher(
        subparsers,
        cmd_name="day-start",
        slash_command="/day-start",
        help_text="Interactive morning planning session (runs /day-start via claude)",
        session_prefix="day-start",
        parents=parents,
    )


def run(args: argparse.Namespace) -> int:
    return args.func(args)
