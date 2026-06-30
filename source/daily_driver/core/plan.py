"""Read time blocks from a daily plan-file's YAML frontmatter.

The plan file is written by Claude during the day-start session; its schema
is the work-planner agent's "Plan File Frontmatter (machine-readable
contract)" section. Each ``plan_items[]`` entry carries a ``time_block`` of
the form ``"HH:MM-HH:MM"`` (or ``null`` for untimed items). This module is the
only Python that parses that schema — pinned to it deliberately, since a field
or format mismatch would silently emit wrong or zero events.

Time blocks are converted to seconds-of-day offsets so the calendar writer can
add them to a midnight ``current date`` in AppleScript, avoiding locale-driven
date-string coercion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Any

import yaml

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

# HH:MM-HH:MM range. The config/scheduler single-time regexes do NOT cover
# this; the reader owns range parsing.
_TIME_BLOCK_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")

# YAML frontmatter delimited by leading `---` ... `---`.
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class CalendarEvent:
    """A single plan time block, resolved to seconds-of-day offsets.

    ``start_seconds`` / ``end_seconds`` are offsets from local midnight on the
    plan's date. ``notes`` carries the idempotency tag plus any source context.
    """

    title: str
    start_seconds: int
    end_seconds: int
    notes: str


def _parse_time_block(value: str) -> tuple[int, int] | None:
    """Return (start_seconds, end_seconds) for an ``HH:MM-HH:MM`` block.

    Returns ``None`` when the string does not match the range format or names
    an out-of-range hour/minute; callers skip such items rather than crash.
    """
    match = _TIME_BLOCK_RE.match(value.strip())
    if match is None:
        return None
    start_h, start_m, end_h, end_m = (int(part) for part in match.groups())
    if start_h > 23 or end_h > 23 or start_m > 59 or end_m > 59:
        return None
    start = start_h * 3600 + start_m * 60
    end = end_h * 3600 + end_m * 60
    if end <= start:
        return None
    return start, end


def _load_frontmatter(plan_path: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter block; ``{}`` when absent or not a mapping."""
    text = plan_path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}
    parsed = yaml.safe_load(match.group(1))
    return parsed if isinstance(parsed, dict) else {}


def read_plan_time_blocks(plan_path: Path, *, day: date_cls) -> list[CalendarEvent]:
    """Read ``plan_items`` time blocks from a plan file into calendar events.

    Items with a null/absent or malformed ``time_block`` are skipped. An empty
    ``plan_items`` (the day-start stub case) or a missing file yields ``[]`` — a
    clean no-op. ``day`` sets the ``daily-plan:<date>`` idempotency tag carried
    in each event's notes.
    """
    if not plan_path.exists():
        log.debug("plan: no plan file at %s; no events", plan_path)
        return []

    frontmatter = _load_frontmatter(plan_path)
    items = frontmatter.get("plan_items") or []
    if not isinstance(items, list):
        log.warning("plan: plan_items is not a list in %s; skipping", plan_path)
        return []

    tag = f"daily-plan:{day.isoformat()}"
    events: list[CalendarEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        time_block = item.get("time_block")
        if not time_block or not isinstance(time_block, str):
            continue
        seconds = _parse_time_block(time_block)
        if seconds is None:
            log.warning("plan: skipping malformed time_block %r", time_block)
            continue
        start_seconds, end_seconds = seconds
        title = str(item.get("text") or "Planned work")
        events.append(
            CalendarEvent(
                title=title,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                notes=tag,
            )
        )
    return events


__all__ = ["CalendarEvent", "read_plan_time_blocks"]
