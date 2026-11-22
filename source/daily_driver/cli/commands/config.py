"""config subcommand: show the settings file that `init` writes."""

from __future__ import annotations

import argparse

from pydantic import ValidationError

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.config import ConfigError
from daily_driver.core.console import Console
from daily_driver.core.workspace import WorkspaceError, default_config_text
from daily_driver.plugins import PLUGINS, enabled_plugins


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "config",
        parents=parents,
        help="Show the default settings file",
    )
    nested = parser.add_subparsers(dest="config_action", metavar="<action>")

    defaults = nested.add_parser(
        "defaults",
        parents=parents,
        help="Print the settings file `init` writes, with every default",
    )
    defaults.add_argument(
        "--plugin",
        action="append",
        choices=[plugin.name for plugin in PLUGINS],
        metavar="<name>",
        help="Include this plugin's settings. Repeat for more than one. "
        "Default: the plugins on in this workspace, or none outside a workspace",
    )
    add_global_flags(defaults)
    defaults.set_defaults(func=_run_defaults)

    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def _workspace_plugins(args: argparse.Namespace) -> set[str]:
    """The plugins on in this workspace; none without a workspace that loads.

    Raises `WorkspaceError` when `--workspace` names a folder that is not a
    workspace: the user asked for that workspace's plugins.
    """
    try:
        workspace = resolve_workspace(args)
    except WorkspaceError:
        if getattr(args, "workspace", None):
            raise
        return set()
    except (ConfigError, ValidationError):
        Console.warning(
            ".dd-config.yaml needs an edit; run `daily-driver doctor` to see it. "
            "Showing the defaults with no plugins."
        )
        return set()
    return {plugin.name for plugin in enabled_plugins(workspace.config)}


def _run_defaults(args: argparse.Namespace) -> int:
    try:
        plugins = set(args.plugin) if args.plugin else _workspace_plugins(args)
    except WorkspaceError as exc:
        Console.error(str(exc))
        return 1
    print(default_config_text(plugins), end="")
    return 0


def run(args: argparse.Namespace) -> int:
    """Bare `config` (no action) prints usage and returns 2."""
    if not hasattr(args, "func") or args.func is run:
        Console.error("usage: daily-driver config {defaults} ...")
        return 2
    return args.func(args)
