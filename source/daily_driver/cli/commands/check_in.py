"""check-in subcommand: interactive mid-day review session via `claude`."""

from __future__ import annotations

import argparse

from daily_driver.cli.commands._claude_session import register_interactive_launcher


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    return register_interactive_launcher(
        subparsers,
        cmd_name="check-in",
        slash_command="/check-in",
        help_text="Interactive mid-day check-in session (runs /check-in via claude)",
        session_prefix="check-in",
        parents=parents,
    )


def run(args: argparse.Namespace) -> int:
    return args.func(args)
