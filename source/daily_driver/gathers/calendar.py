from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from daily_driver.core.logging import get_logger, log_query_window
from daily_driver.integrations import icalbuddy

log = get_logger(__name__)

# Matches ISO-like datetime strings e.g. "2026-04-20 09:00" or "2026-04-20T09:00"
_DATETIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")
# Matches an HH:MM time anywhere in a line (used to recover a range end time
# that follows the start on the same line).
_TIME_IN_LINE_RE = re.compile(r"(\d{2}:\d{2})")
# Matches standalone HH:MM time lines (for icalBuddy's "-tf %H:%M" output)
_TIME_RE = re.compile(r"^\s*(\d{2}:\d{2})\s*(?:-\s*(\d{2}:\d{2}))?\s*$")
# Matches a bare ISO date line (no time) — an all-day event
_BARE_DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*$")
# Matches a line that is only an ISO date, optionally with a time. Used to track
# the current day from genuine date lines, so digits embedded in a URL or title
# can't masquerade as a date for later events.
_DATE_LINE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:\s*$|[T ]\d{2}:\d{2})")

# Markers that strongly suggest icalBuddy printed its usage banner instead of
# parseable event data. "USAGE:" is anchored to the very start of output
# (after lstrip) to avoid matching events that happen to mention "USAGE:" in
# their title or body. The other two markers are distinctive enough to be
# unambiguous wherever they appear.
_USAGE_BODY_MARKERS = ("icalBuddy man page", "Originally by Ali Rantakari")


def _scan_range_end(rest: str, start_date: str) -> datetime | None:
    """Recover a range end time from the remainder of a start line.

    icalBuddy prints a dated range on a single line as
    "2026-04-21 10:00 - 11:00" (the date appears once) and occasionally as
    "2026-04-21 10:00 - 2026-04-21 11:00". The end may therefore be a full
    datetime or a bare HH:MM that shares the start's date.
    """
    m = _DATETIME_RE.search(rest)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    t = _TIME_IN_LINE_RE.search(rest)
    if t:
        try:
            return datetime.strptime(f"{start_date} {t.group(1)}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return None


def _looks_like_usage_text(stdout: str) -> bool:
    if stdout.lstrip().startswith("USAGE:"):
        return True
    head = stdout[:1024]
    return any(marker in head for marker in _USAGE_BODY_MARKERS)


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
                parsed: datetime | None = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            except ValueError:
                parsed = None
            if parsed is not None:
                if start is None:
                    start = parsed
                    # A dated range may sit on this one line; recover its end
                    # time so timed events keep their duration.
                    if end is None:
                        end = _scan_range_end(line[m.end() :], m.group(1))
                elif end is None:
                    end = parsed
            continue

        # Bare date line (no time) — an all-day event; anchor start at midnight.
        bd = _BARE_DATE_RE.match(line)
        if bd:
            if start is None:
                try:
                    start = datetime.strptime(bd.group(1), "%Y-%m-%d")
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


def _split_event_blocks(stdout: str) -> list[str]:
    """Group icalBuddy output into one block per event.

    With the flags this app uses (`-b ""`), icalBuddy 1.10.x prints each event
    as a title at column 0 followed by indented (`datetime`, `location`)
    property lines, with NO blank line between events — so the previous
    blank-line split (`\\n{2,}`) collapsed the whole calendar into a single
    block. Group by the unindented title line instead: a non-blank line at
    column 0 starts a new event; indented lines (and skipped blank lines) belong
    to the current one. This also handles the older blank-line-separated format,
    where titles are still at column 0.
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if line[:1] not in (" ", "\t"):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def gather_events(since: datetime, until: datetime) -> list[CalendarEvent]:
    """Read macOS Calendar via icalBuddy. Returns [] if icalBuddy is missing."""
    log_query_window(log, "calendar", since, until)
    stdout = icalbuddy.events_between(since, until)
    if stdout is None:
        return []

    if _looks_like_usage_text(stdout):
        log.warning(
            "calendar: icalBuddy returned usage/help text — invocation is wrong; "
            "got: %r",
            stdout.strip().splitlines()[0][:120] if stdout else "",
        )
        return []

    events: list[CalendarEvent] = []
    current_date = since.strftime("%Y-%m-%d")

    # One block per event (icalBuddy groups by an unindented title line, not by
    # blank lines — see _split_event_blocks).
    blocks = _split_event_blocks(stdout)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # A standalone date header (e.g. "Monday, April 20, 2026:") is not an
        # event; skip it rather than logging it as an unparseable block. (These
        # flags don't emit headers, but tolerate them if a future flag does.)
        if re.match(
            r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,?\s+\w+\s+\d{1,2},?\s+\d{4}\s*:?$",
            block,
            re.IGNORECASE,
        ):
            log.debug("calendar: skipping date-header block: %r", block[:80])
            continue

        # Track the current day only from a genuine date line (bare date or
        # date+time at the start of a line), never from digits embedded in a
        # URL or title elsewhere in the block.
        for line in block.splitlines():
            date_line = _DATE_LINE_RE.match(line)
            if date_line:
                current_date = date_line.group(1)
                break

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
