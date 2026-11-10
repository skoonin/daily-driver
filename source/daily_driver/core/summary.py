"""Core logic for the `summary` subcommand.

Handles range parsing, the list of workspace files a period covers, and
prompt rendering. Range windows use `date` (not `datetime`) — day-granular by
design — so timezone handling does not apply.

Summary is an agent task: the prompt names the files and the provider reads
them itself, so no file contents ride in the prompt.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from daily_driver.core import dates as _dates
from daily_driver.core.workspace import Workspace, daily_file

_TEMPLATE_DIR = Path(__file__).parent.parent / "resources" / "templates"
_SUMMARY_TEMPLATE = "summary.md.j2"


def parse_range(spec: str) -> tuple[date, date]:
    """Parse a range specifier into (start, end) dates, inclusive.

    Thin delegate to :func:`daily_driver.core.dates.parse_range` so summary's
    grammar matches every other date-accepting flag (today/yesterday/tomorrow,
    week/month/quarter/year, Nd|Nw|Nm|Ny, ISO single, ISO range).
    """
    return _dates.parse_range(spec)


def summary_files(
    workspace: Workspace, range_start: date, range_end: date, *, tracker: Path
) -> list[Path]:
    """Existing plan and notes files for each day in the range, then the tracker."""
    files: list[Path] = []
    day = range_start
    while day <= range_end:
        for kind in ("plan", "notes"):
            path = daily_file(workspace.root, day, kind)
            if path.is_file():
                files.append(path)
        day += timedelta(days=1)
    if tracker.is_file():
        files.append(tracker)
    return files


def render_prompt(
    *,
    range_start: date,
    range_end: date,
    detail: str = "med",
    match: list[str] | None = None,
    files: list[Path] | None = None,
) -> str:
    """Render the summary prompt template to a string.

    detail: "low" | "med" | "high" — controls verbosity section in template.
    match: optional keyword filters included in the prompt.
    files: workspace files the provider reads for the period.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tmpl = env.get_template(_SUMMARY_TEMPLATE)
    return tmpl.render(
        range_start=range_start,
        range_end=range_end,
        detail=detail,
        match=match or [],
        files=files or [],
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
