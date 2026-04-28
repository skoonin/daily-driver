"""Core logic for the `summary` subcommand.

Handles range parsing and prompt rendering. Range windows use `date` (not
`datetime`) — day-granular by design — so timezone handling does not apply.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_SUMMARY_TEMPLATE = "summary.md.j2"

# Named range keywords
_NAMED_RANGES = ("today", "yesterday", "week", "month")


def parse_range(spec: str) -> tuple[date, date]:
    """Parse a range specifier into (start, end) dates, inclusive.

    Accepted forms:
      today | yesterday | week | month
      YYYY-MM-DD               (single day)
      YYYY-MM-DD:YYYY-MM-DD    (explicit range)

    Raises ValueError for unrecognised or inverted specs.
    """
    today = date.today()

    if spec == "today":
        return today, today

    if spec == "yesterday":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday

    if spec == "week":
        # ISO week: Monday to today (or Sunday if today is Sunday)
        weekday = today.weekday()  # 0=Mon, 6=Sun
        start = today - timedelta(days=weekday)
        return start, today

    if spec == "month":
        start = today.replace(day=1)
        return start, today

    if ":" in spec:
        parts = spec.split(":", 1)
        try:
            start = date.fromisoformat(parts[0])
            end = date.fromisoformat(parts[1])
        except ValueError:
            raise ValueError(
                f"invalid range spec: {spec!r}; expected YYYY-MM-DD:YYYY-MM-DD"
            )
        if start > end:
            raise ValueError(f"start date {start} must not be after end date {end}")
        return start, end

    # Try as a single ISO date
    try:
        d = date.fromisoformat(spec)
        return d, d
    except ValueError:
        pass

    raise ValueError(
        f"unrecognised range spec: {spec!r}; "
        f"expected one of {_NAMED_RANGES} or YYYY-MM-DD or YYYY-MM-DD:YYYY-MM-DD"
    )


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
