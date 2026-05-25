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

from rich.console import Console

from daily_driver.cli._common import add_global_flags, resolve_workspace

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
    from daily_driver.scraper.sources import SCRAPERS

    return {
        "sources": sorted(SCRAPERS),
        "consumers": ["jobs run --sources", "scraper.sources.<name>.enabled"],
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


def _render_full(console: Console, payload: dict[str, Any]) -> None:
    console.print("[bold]daily-driver — reference[/bold]\n")

    console.print("[bold]Commands[/bold] (run `daily-driver <cmd> --help` for flags):")
    for entry in payload["commands"]:
        console.print(f"  {entry['name']:<14} {entry['summary']}")
    console.print("")

    statuses = payload["statuses"]
    console.print("[bold]Statuses[/bold] (consumers: tracker add/update --status):")
    console.print(f"  recommended: {', '.join(statuses['recommended'])}")
    if statuses["in_use"]:
        console.print(f"  in-use:      {', '.join(statuses['in_use'])}")
    console.print("")

    cats = payload["categories"]
    console.print("[bold]Categories[/bold] (consumer: tracker add --category):")
    if cats.get("note"):
        console.print(f"  {cats['note']}")
    else:
        listed = ", ".join(cats["categories"]) or "(none configured)"
        console.print(f"  {listed}")
        if cats.get("default"):
            console.print(f"  default: {cats['default']}")
    console.print("")

    srcs = payload["sources"]
    console.print("[bold]Sources[/bold] (consumer: jobs run --sources):")
    console.print(f"  {', '.join(srcs['sources'])}")
    console.print("")

    dates = payload["dates"]
    console.print("[bold]Dates[/bold] (date-spec grammar):")
    console.print(f"  tokens:    {', '.join(dates['tokens'])}")
    console.print(f"  consumers: {', '.join(dates['consumers'])}")
    console.print("")

    console.print("[bold]Cadences[/bold] (consumer: recurring_tasks[].cadence):")
    console.print(f"  {', '.join(payload['cadences'])}")


def _render_topic(console: Console, topic: str, payload: dict[str, Any]) -> None:
    if topic == "commands":
        for entry in payload["commands"]:
            console.print(f"  {entry['name']:<14} {entry['summary']}")
        return

    if topic == "statuses":
        s = payload["statuses"]
        console.print(f"recommended: {', '.join(s['recommended'])}")
        if s["in_use"]:
            console.print(f"in-use:      {', '.join(s['in_use'])}")
        else:
            console.print(
                "in-use:      (no workspace; only recommended statuses shown)"
            )
        console.print(f"consumers:   {', '.join(s['consumers'])}")
        return

    if topic == "categories":
        c = payload["categories"]
        if c.get("note"):
            console.print(c["note"])
        else:
            console.print(", ".join(c["categories"]) or "(none configured)")
            if c.get("default"):
                console.print(f"default: {c['default']}")
        console.print(f"consumers: {', '.join(c['consumers'])}")
        return

    if topic == "sources":
        s = payload["sources"]
        console.print(", ".join(s["sources"]))
        console.print(f"consumers: {', '.join(s['consumers'])}")
        return

    if topic == "dates":
        d = payload["dates"]
        console.print(f"tokens:    {', '.join(d['tokens'])}")
        console.print(f"consumers: {', '.join(d['consumers'])}")
        return

    if topic == "cadences":
        console.print(", ".join(payload["cadences"]))
        return


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

    console = Console(stderr=False)
    if topic is None:
        _render_full(console, payload)
    else:
        _render_topic(console, topic, payload)
    return 0
