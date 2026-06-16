"""day-end subcommand: interactive end-of-day review session via `claude`."""

from __future__ import annotations

import argparse

from daily_driver.cli.commands._claude_session import register_interactive_launcher


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    return register_interactive_launcher(
        subparsers,
        cmd_name="day-end",
        slash_command="/daily-driver:day-end",
        help_text="Interactive end-of-day review session (runs /daily-driver:day-end via claude)",
        session_prefix="day-end",
        parents=parents,
    )


def run(args: argparse.Namespace) -> int:
    return args.func(args)
