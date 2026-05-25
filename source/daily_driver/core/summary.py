"""Core logic for the `summary` subcommand.

Handles range parsing and prompt rendering. Range windows use `date` (not
`datetime`) — day-granular by design — so timezone handling does not apply.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from daily_driver.core import dates as _dates
from daily_driver.core.config_models import Config

_TEMPLATE_DIR = Path(__file__).parent.parent / "resources" / "templates"
_SUMMARY_TEMPLATE = "summary.md.j2"


def ai_routing_dict(config: Config) -> dict[str, Any]:
    """Project a validated `Config` into the dict `ai_provider.invoke_for` reads.

    `invoke_for(config=...)` extracts the `ai:` block via `resolve_ai_config`,
    so summary must hand it the same validated config every other command uses
    rather than a second `yaml.safe_load` of `.dd-config.yaml` with its own
    notion of "valid".
    """
    return config.model_dump()


def parse_range(spec: str) -> tuple[date, date]:
    """Parse a range specifier into (start, end) dates, inclusive.

    Thin delegate to :func:`daily_driver.core.dates.parse_range` so summary's
    grammar matches every other date-accepting flag (today/yesterday/tomorrow,
    week/month/quarter/year, Nd|Nw|Nm|Ny, ISO single, ISO range).
    """
    return _dates.parse_range(spec)


def render_prompt(
    *,
    range_start: date,
    range_end: date,
    detail: str = "med",
    match: list[str] | None = None,
) -> str:
    """Render the summary prompt template to a string.

    detail: "low" | "med" | "high" — controls verbosity section in template.
    match: optional keyword filters included in the prompt.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        keep_trailing_newline=True,
    )
    tmpl = env.get_template(_SUMMARY_TEMPLATE)
    return tmpl.render(
        range_start=range_start,
        range_end=range_end,
        detail=detail,
        match=match or [],
    )


def build_json_bundle(
    *,
    range_start: date,
    range_end: date,
    detail: str = "med",
    match: list[str] | None = None,
) -> dict[str, Any]:
    """Build the --json output bundle (schema version 1)."""
    return {
        "schema": 1,
        "data": {
            "range": {
                "start": range_start.isoformat(),
                "end": range_end.isoformat(),
            },
            "detail": detail,
            "match": match or [],
        },
    }
