from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

# Matches ISO-like datetime strings e.g. "2026-04-20 09:00" or "2026-04-20T09:00"
_DATETIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")
# Matches standalone HH:MM time lines (for icalBuddy's "-tf %H:%M" output)
_TIME_RE = re.compile(r"^\s*(\d{2}:\d{2})\s*(?:-\s*(\d{2}:\d{2}))?\s*$")


class CalendarEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    start: datetime
    end: datetime | None = None
    calendar: str | None = None
    location: str | None = None


def _parse_event_block(block: str, event_date: str) -> CalendarEvent | None:
    """Parse a single icalBuddy paragraph into a CalendarEvent.

    icalBuddy output is not machine-stable; we match defensively.
    """
    lines = [ln for ln in block.strip().splitlines() if ln.strip()]
    if not lines:
        return None

    title = lines[0].strip()
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None

    for line in lines[1:]:
        # Full ISO datetime on the line
        m = _DATETIME_RE.search(line)
        if m:
            dt_str = f"{m.group(1)} {m.group(2)}"
            try:
                parsed = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                if start is None:
                    start = parsed
                elif end is None:
                    end = parsed
            except ValueError:
                pass
            continue

        # Time-only line — combine with the block's date context
        tm = _TIME_RE.match(line)
        if tm and event_date:
            try:
                start = datetime.strptime(
                    f"{event_date} {tm.group(1)}", "%Y-%m-%d %H:%M"
                )
                if tm.group(2):
                    end = datetime.strptime(
                        f"{event_date} {tm.group(2)}", "%Y-%m-%d %H:%M"
                    )
            except ValueError:
                pass
            continue

        # Location field
        if re.search(r"location", line, re.IGNORECASE):
            loc_part = re.sub(r"(?i)location\s*:\s*", "", line).strip()
            if loc_part:
                location = loc_part

    if start is None:
        return None

    return CalendarEvent(title=title, start=start, end=end, location=location)


def gather_events(since: datetime, until: datetime) -> list[CalendarEvent]:
    """Read macOS Calendar via icalBuddy. Returns [] if icalBuddy is missing."""
    if shutil.which("icalBuddy") is None:
        log.warning("calendar: icalBuddy not found; skipping calendar gather")
        return []

    cmd = [
        "icalBuddy",
        "-iep",
        "title,datetime,location",
        "-nc",
        "-b",
        "",
        "-df",
        "%Y-%m-%d",
        "-tf",
        "%H:%M",
        f"eventsFrom:{since.strftime('%Y-%m-%d')}",
        "to:",
        until.strftime("%Y-%m-%d"),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=30
        )
    except subprocess.TimeoutExpired:
        log.warning("calendar: icalbuddy timed out after 30s; returning no events")
        return []

    events: list[CalendarEvent] = []
    current_date = since.strftime("%Y-%m-%d")

    # icalBuddy separates events with blank lines
    blocks = re.split(r"\n{2,}", result.stdout)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Track date headers icalBuddy may emit (e.g. "Monday, April 20, 2026:")
        date_header = re.match(
            r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,?\s+\w+\s+\d{1,2},?\s+(\d{4})\s*:?$",
            block,
            re.IGNORECASE,
        )
        if date_header:
            # date_header detected; current_date updated via ISO match below
            pass

        # Pick up "2026-04-20" anywhere in the block to track current day
        iso_date = re.search(r"(\d{4}-\d{2}-\d{2})", block)
        if iso_date:
            current_date = iso_date.group(1)

        try:
            event = _parse_event_block(block, current_date)
        except Exception as exc:
            log.warning(
                "calendar: could not parse event block: %r (%s)", block[:80], exc
            )
            continue

        if event is None:
            log.warning("calendar: could not parse event block: %r", block[:80])
            continue

        events.append(event)

    return events
