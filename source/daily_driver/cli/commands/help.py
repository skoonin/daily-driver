"""help subcommand: reference for commands + discoverable values.

Distinct from argparse `--help` (usage). This command surfaces the value
groups callers actually need to discover at the prompt: tracker statuses
(recommended + in-use), tracker categories (from config), each plugin's own
topics, and date-spec grammar.

`--json` produces a structured payload for scripting / tab-completion
generators. Topic filtering (`help statuses`, etc.) keeps interactive use
scannable.

A plugin that is off in the workspace, or any plugin outside a workspace,
adds nothing: no topic, status, date flag, or command.
"""

from __future__ import annotations

import argparse
import importlib
from typing import Any

from rich.console import Console as RichConsole

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.console import Console
from daily_driver.plugins._base import HelpTopic, Plugin


def _topics(plugins: tuple[Plugin, ...]) -> tuple[str, ...]:
    """Topic names accepted by `help <topic>`, in full-reference order.

    Commands come first (the most-used surface), then value groups by how
    often people look them up.
    """
    return (
        "commands",
        "statuses",
        "categories",
        *(topic.name for topic in _plugin_topics(plugins)),
        "dates",
    )


def _plugin_topics(plugins: tuple[Plugin, ...]) -> tuple[HelpTopic, ...]:
    return tuple(topic for plugin in plugins for topic in plugin.help_topics)


# Static grammar — these don't change per-workspace, and the consumers list
# is hand-curated so users see which flag accepts each token.
_DATE_TOKENS = (
    "today",
    "yesterday",
    "week",
    "month",
    "quarter",
    "year",
    "Nd",
    "Nw",
    "Nm",
    "Ny",
    "YYYY-MM-DD",
)
_DATE_CONSUMERS = (
    "tracker list --since",
    "tracker prune --older-than",
    "gather calendar --since/--until",
    "gather git --since/--until",
    "summary --range",
)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "help",
        parents=parents,
        help="Show reference: subcommands and discoverable values",
    )
    # No argparse `choices`: the topics depend on the plugins that are on in
    # the workspace, which is known only after parsing. `run` checks the topic.
    parser.add_argument(
        "topic",
        nargs="?",
        metavar="<topic>",
        default=None,
        help="Show one topic of the full reference, such as commands, "
        "statuses, categories, or dates; `daily-driver help` shows every "
        "topic (default: all of it).",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit structured JSON instead of Rich text.",
    )
    add_global_flags(parser)
    parser.set_defaults(func=run, help_parser=parser)
    return parser


# ---------------------------------------------------------------------------
# Workspace-derived value resolution (graceful when no workspace exists)
# ---------------------------------------------------------------------------


def _resolve_workspace(args: argparse.Namespace) -> Any | None:
    """Return a Workspace if discoverable, else None.

    `help` must work without a workspace — it's the discovery surface that
    new users hit first. Failure to find one is informational, not fatal,
    unless `--workspace` names a folder that is not a workspace. A config
    that needs an edit before it can load is not fatal either: `help` warns and shows
    the values it would show without a workspace. Other config errors still
    stop it.
    """
    from daily_driver.core.config import ConfigError
    from daily_driver.core.workspace import WorkspaceError

    try:
        return resolve_workspace(args)
    except WorkspaceError:
        if getattr(args, "workspace", None):
            raise
        return None
    except ConfigError:
        Console.warning(
            ".dd-config.yaml needs an edit; run `daily-driver doctor` to see it. "
            "Showing values without your workspace."
        )
        return None


def _plugins_on(workspace: Any | None) -> tuple[Plugin, ...]:
    if workspace is None:
        return ()
    from daily_driver.plugins import enabled_plugins

    return tuple(enabled_plugins(workspace.config))


def _statuses(workspace: Any | None, plugins: tuple[Plugin, ...]) -> dict[str, Any]:
    from daily_driver.core.tracker import RECOMMENDED_STATUSES, Tracker

    in_use: list[str] = []
    if workspace is not None:
        try:
            entries = Tracker(workspace).load().entries
            seen: set[str] = set()
            for e in entries:
                if e.status not in seen:
                    seen.add(e.status)
                    in_use.append(e.status)
        except Exception:  # noqa: BLE001
            # Tracker file unreadable: degrade to recommended-only.
            in_use = []
    return {
        "recommended": list(RECOMMENDED_STATUSES),
        "by_category": {
            category: list(statuses)
            for plugin in plugins
            for category, statuses in plugin.tracker_statuses.items()
        },
        "in_use": in_use,
        "consumers": ["tracker add --status", "tracker update --status"],
    }


def _categories(workspace: Any | None) -> dict[str, Any]:
    if workspace is None:
        return {
            "categories": [],
            "default": None,
            "note": ("(no workspace; configure tracker.categories in .dd-config.yaml)"),
            "consumers": ["tracker add --category", "tracker list --category"],
        }
    cfg = workspace.config.tracker
    return {
        "categories": list(cfg.categories.keys()),
        "default": cfg.default_category,
        "consumers": ["tracker add --category", "tracker list --category"],
    }


def _plugin_topic(topic: HelpTopic) -> dict[str, Any]:
    module_path, _, attr = topic.values.rpartition(".")
    values = getattr(importlib.import_module(module_path), attr)
    return {topic.name: values(), "consumers": list(topic.consumers)}


def _dates(plugins: tuple[Plugin, ...]) -> dict[str, Any]:
    return {
        "tokens": list(_DATE_TOKENS),
        "consumers": [
            *_DATE_CONSUMERS,
            *(flag for plugin in plugins for flag in plugin.date_consumers),
        ],
    }


def _commands(plugins: tuple[Plugin, ...]) -> list[dict[str, Any]]:
    """Return the top-level subcommand list with a one-line summary each.

    Imported lazily to avoid pulling every command module at startup. We
    walk the registered (cmd_name, module_path) table so this stays in lockstep
    with cli.cli._COMMANDS.
    """
    from daily_driver.cli.cli import _ALIASES, _COMMANDS
    from daily_driver.plugins import PLUGINS

    off = {plugin.command_name for plugin in PLUGINS if plugin not in plugins}
    out: list[dict[str, Any]] = []
    for name, module_path in _COMMANDS:
        if name in off:
            continue
        try:
            mod = importlib.import_module(module_path)
        except Exception:  # noqa: BLE001
            continue
        # Use the module docstring's first line as the help blurb, falling
        # back to a plain "no description" rather than crashing.
        doc = (mod.__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else "(no description)"
        out.append(
            {"name": name, "aliases": list(_ALIASES.get(name, ())), "summary": summary}
        )
    return out


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_payload(
    workspace: Any | None, plugins: tuple[Plugin, ...]
) -> dict[str, Any]:
    return {
        "commands": _commands(plugins),
        "statuses": _statuses(workspace, plugins),
        "categories": _categories(workspace),
        **{topic.name: _plugin_topic(topic) for topic in _plugin_topics(plugins)},
        "dates": _dates(plugins),
    }


def _emit_json(payload: dict[str, Any]) -> None:
    Console.emit_json(payload)


# ---------------------------------------------------------------------------
# Rich rendering — terse, scannable
# ---------------------------------------------------------------------------


# Section headers shown only in the full reference (one per topic). The topic
# view drops these — the topic name is already the user's query. A plugin
# topic carries its own header.
_TOPIC_HEADERS = {
    "commands": "[bold]Commands[/bold] (run `daily-driver <cmd> --help` for flags):",
    "statuses": "[bold]Statuses[/bold] (consumers: tracker add/update --status):",
    "categories": "[bold]Categories[/bold] (consumer: tracker add --category):",
    "dates": "[bold]Dates[/bold] (date-spec grammar):",
}


def _render_topic(
    console: RichConsole,
    topic: str,
    payload: dict[str, Any],
    *,
    full: bool,
    header: str = "",
) -> None:
    """Render one topic. The full reference loops this per topic with a
    section header and indented body; the standalone topic view drops the
    header/indent and appends the per-topic `consumers:` line.

    `full` toggles the two presentations so both surfaces share one renderer
    while preserving their distinct output.
    """
    indent = "  " if full else ""
    if full:
        console.print(header)

    if topic == "commands":
        for entry in payload["commands"]:
            aliases = "".join(f" ({alias})" for alias in entry["aliases"])
            console.print(f"{indent}{entry['name'] + aliases:<18} {entry['summary']}")

    elif topic == "statuses":
        s = payload["statuses"]
        console.print(f"{indent}recommended: {', '.join(s['recommended'])}")
        for category, statuses in s["by_category"].items():
            console.print(f"{indent}{category + ':':<12} {', '.join(statuses)}")
        if s["in_use"]:
            console.print(f"{indent}in-use:      {', '.join(s['in_use'])}")
        elif not full:
            console.print(
                "in-use:      (no workspace; only recommended statuses shown)"
            )
        if not full:
            console.print(f"consumers:   {', '.join(s['consumers'])}")

    elif topic == "categories":
        c = payload["categories"]
        if c.get("note"):
            console.print(f"{indent}{c['note']}")
        else:
            console.print(
                f"{indent}{', '.join(c['categories']) or '(none configured)'}"
            )
            if c.get("default"):
                console.print(f"{indent}default: {c['default']}")
        if not full:
            console.print(f"consumers: {', '.join(c['consumers'])}")

    elif topic == "dates":
        d = payload["dates"]
        console.print(f"{indent}tokens:    {', '.join(d['tokens'])}")
        console.print(f"{indent}consumers: {', '.join(d['consumers'])}")

    else:
        plugin_topic = payload[topic]
        console.print(f"{indent}{', '.join(plugin_topic[topic])}")
        if not full:
            console.print(f"consumers: {', '.join(plugin_topic['consumers'])}")


def _render_full(
    console: RichConsole, payload: dict[str, Any], plugins: tuple[Plugin, ...]
) -> None:
    headers = {
        **_TOPIC_HEADERS,
        **{topic.name: topic.header for topic in _plugin_topics(plugins)},
    }
    topics = _topics(plugins)
    console.print("[bold]daily-driver — reference[/bold]\n")
    for i, topic in enumerate(topics):
        _render_topic(console, topic, payload, full=True, header=headers[topic])
        # Blank line between sections, but not after the last.
        if i != len(topics) - 1:
            console.print("")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    from daily_driver.core.workspace import WorkspaceError

    try:
        workspace = _resolve_workspace(args)
    except WorkspaceError as exc:
        Console.error(str(exc))
        return 1
    plugins = _plugins_on(workspace)
    topic = args.topic
    topics = _topics(plugins)
    if topic is not None and topic not in topics:
        args.help_parser.error(
            f"argument <topic>: invalid choice: {topic!r} "
            f"(choose from {', '.join(topics)})"
        )
    payload = _build_payload(workspace, plugins)

    emit_json = getattr(args, "json", False)

    if emit_json:
        if topic is None:
            _emit_json(payload)
        else:
            # Topic JSON: same envelope, but data scoped to the topic key
            # plus its peer (e.g. statuses includes recommended/in_use/consumers).
            scoped: dict[str, Any] = {topic: payload[topic]}
            _emit_json(scoped)
        return 0

    console = Console.get_user_console()
    if topic is None:
        _render_full(console, payload, plugins)
    else:
        _render_topic(console, topic, payload, full=False)
    return 0
