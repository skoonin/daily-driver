"""help subcommand: reference for commands + discoverable values.

Distinct from argparse `--help` (usage). This command surfaces the value
groups callers actually need to discover at the prompt: tracker statuses
(recommended + in-use), tracker categories (from config), scraper sources,
date-spec grammar, and recurring-task cadences.

`--json` produces a structured payload for scripting / tab-completion
generators. Topic filtering (`help statuses`, etc.) keeps interactive use
scannable.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from rich.console import Console as RichConsole

from daily_driver.cli._common import add_global_flags, resolve_workspace
from daily_driver.core.console import Console

# Topic names accepted by `help <topic>`. Order is the rendering order in
# the full reference, so keep it grouped: commands first (most-used surface),
# then value groups by frequency.
_TOPICS = ("commands", "statuses", "categories", "sources", "dates", "cadences")

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
    "jobs prune --older-than",
    "gather calendar --since/--until",
    "gather git --since/--until",
    "summary --range",
)

_CADENCES = ("daily", "weekly", "monthly")
_CADENCE_CONSUMERS = ("recurring_tasks[].cadence (in .dd-config.yaml)",)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "help",
        parents=parents,
        help="Show reference: subcommands and discoverable values",
    )
    parser.add_argument(
        "topic",
        nargs="?",
        choices=_TOPICS,
        default=None,
        help="Focus on a single topic (default: full reference).",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        default=False,
        help="Emit structured JSON instead of Rich text.",
    )
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


# ---------------------------------------------------------------------------
# Workspace-derived value resolution (graceful when no workspace exists)
# ---------------------------------------------------------------------------


def _resolve_workspace(args: argparse.Namespace) -> Any | None:
    """Return a Workspace if discoverable, else None.

    `help` must work without a workspace — it's the discovery surface that
    new users hit first. Failure to find one is informational, not fatal.
    """
    from daily_driver.core.workspace import WorkspaceError

    try:
        return resolve_workspace(args)
    except WorkspaceError:
        return None


def _statuses(workspace: Any | None) -> dict[str, Any]:
    from daily_driver.core.tracker import (
        JOB_RECOMMENDED_STATUSES,
        RECOMMENDED_STATUSES,
        Tracker,
    )

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
        "job_recommended": list(JOB_RECOMMENDED_STATUSES),
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


def _sources() -> dict[str, Any]:
    from daily_driver.plugins.job_search.scraper.sources import SCRAPERS

    return {
        "sources": sorted(SCRAPERS),
        "consumers": ["jobs run --sources", "sources.<name>.enabled"],
    }


def _dates() -> dict[str, Any]:
    return {
        "tokens": list(_DATE_TOKENS),
        "consumers": list(_DATE_CONSUMERS),
    }


def _cadences() -> dict[str, Any]:
    return {
        "values": list(_CADENCES),
        "consumers": list(_CADENCE_CONSUMERS),
    }


def _commands() -> list[dict[str, str]]:
    """Return the top-level subcommand list with a one-line summary each.

    Imported lazily to avoid pulling every command module at startup. We
    walk the registered (cmd_name, module_path) table so this stays in lockstep
    with cli.cli._COMMANDS.
    """
    import importlib

    from daily_driver.cli.cli import _COMMANDS

    out: list[dict[str, str]] = []
    for name, module_path in _COMMANDS:
        try:
            mod = importlib.import_module(module_path)
        except Exception:  # noqa: BLE001
            continue
        # Use the module docstring's first line as the help blurb, falling
        # back to a plain "no description" rather than crashing.
        doc = (mod.__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else "(no description)"
        out.append({"name": name, "summary": summary})
    return out


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_payload(workspace: Any | None) -> dict[str, Any]:
    return {
        "commands": _commands(),
        "statuses": _statuses(workspace),
        "categories": _categories(workspace),
        "sources": _sources(),
        "dates": _dates(),
        "cadences": _cadences()["values"],
    }


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps({"schema": 1, "data": payload}, indent=2))


# ---------------------------------------------------------------------------
# Rich rendering — terse, scannable
# ---------------------------------------------------------------------------


# Section headers shown only in the full reference (one per topic). The topic
# view drops these — the topic name is already the user's query.
_TOPIC_HEADERS = {
    "commands": "[bold]Commands[/bold] (run `daily-driver <cmd> --help` for flags):",
    "statuses": "[bold]Statuses[/bold] (consumers: tracker add/update --status):",
    "categories": "[bold]Categories[/bold] (consumer: tracker add --category):",
    "sources": "[bold]Sources[/bold] (consumer: jobs run --sources):",
    "dates": "[bold]Dates[/bold] (date-spec grammar):",
    "cadences": "[bold]Cadences[/bold] (consumer: recurring_tasks[].cadence):",
}


def _render_topic(
    console: RichConsole, topic: str, payload: dict[str, Any], *, full: bool
) -> None:
    """Render one topic. The full reference loops this per topic with a
    section header and indented body; the standalone topic view drops the
    header/indent and appends the per-topic `consumers:` line.

    `full` toggles the two presentations so both surfaces share one renderer
    while preserving their distinct output.
    """
    indent = "  " if full else ""
    if full:
        console.print(_TOPIC_HEADERS[topic])

    if topic == "commands":
        for entry in payload["commands"]:
            console.print(f"{indent}{entry['name']:<14} {entry['summary']}")

    elif topic == "statuses":
        s = payload["statuses"]
        console.print(f"{indent}recommended: {', '.join(s['recommended'])}")
        console.print(f"{indent}job:         {', '.join(s['job_recommended'])}")
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

    elif topic == "sources":
        s = payload["sources"]
        console.print(f"{indent}{', '.join(s['sources'])}")
        if not full:
            console.print(f"consumers: {', '.join(s['consumers'])}")

    elif topic == "dates":
        d = payload["dates"]
        console.print(f"{indent}tokens:    {', '.join(d['tokens'])}")
        console.print(f"{indent}consumers: {', '.join(d['consumers'])}")

    elif topic == "cadences":
        console.print(f"{indent}{', '.join(payload['cadences'])}")


def _render_full(console: RichConsole, payload: dict[str, Any]) -> None:
    console.print("[bold]daily-driver — reference[/bold]\n")
    for i, topic in enumerate(_TOPICS):
        _render_topic(console, topic, payload, full=True)
        # Blank line between sections, but not after the last.
        if i != len(_TOPICS) - 1:
            console.print("")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args)
    payload = _build_payload(workspace)
    topic = args.topic

    emit_json = getattr(args, "json", False)

    if emit_json:
        if topic is None:
            _emit_json(payload)
        else:
            # Topic JSON: same envelope, but data scoped to the topic key
            # plus its peer (e.g. statuses includes recommended/in_use/consumers).
            scoped: dict[str, Any] = {topic: payload[topic]}
            print(
                json.dumps({"schema": 1, "data": scoped}, indent=2),
                file=sys.stdout,
            )
        return 0

    console = Console.get_user_console()
    if topic is None:
        _render_full(console, payload)
    else:
        _render_topic(console, topic, payload, full=False)
    return 0
