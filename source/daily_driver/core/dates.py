"""Unified date / range parser used by every date-accepting CLI flag.

Grammar accepted everywhere:

    today | yesterday | tomorrow
    week | month | quarter | year       (start-of-period to today)
    Nd | Nw | Nm | Ny                   (relative duration; positive only)
    YYYY-MM-DD                          (ISO single day)
    YYYY-MM-DD:YYYY-MM-DD               (ISO range, inclusive)

All parsing is local-day relative to ``daily_driver.core.clock.now()`` so
``FROZEN_TIME`` works in tests without monkey-patching.

`Nm` means months (per #6); minutes are not supported. If we ever add minute
math, use ``min`` as the suffix.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from daily_driver.core import clock

_DURATION_RE = re.compile(r"^(\d+)([dwmy])$")
_NAMED_PERIODS = ("week", "month", "quarter", "year")
_RELATIVE_DAYS = {"today": 0, "yesterday": -1, "tomorrow": 1}


def _today() -> date:
    return clock.today()


def _add_months(d: date, months: int) -> date:
    """Add `months` to d, clamping the day to the new month's max."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _quarter_start(d: date) -> date:
    quarter = (d.month - 1) // 3
    return date(d.year, quarter * 3 + 1, 1)


def parse_since(spec: str, *, anchor: date | None = None) -> date:
    """Resolve a single point-in-time spec to its start date.

    Used for `--since` flags and equivalent. Returns the *start* of the
    implied window: ``week`` returns the Monday on/before anchor;
    ``7d`` returns 7 days before anchor; ``yesterday`` returns yesterday.
    """
    anchor = anchor or _today()
    spec = spec.strip()

    if spec in _RELATIVE_DAYS:
        return anchor + timedelta(days=_RELATIVE_DAYS[spec])

    if spec == "week":
        return anchor - timedelta(days=anchor.weekday())
    if spec == "month":
        return anchor.replace(day=1)
    if spec == "quarter":
        return _quarter_start(anchor)
    if spec == "year":
        return date(anchor.year, 1, 1)

    m = _DURATION_RE.match(spec)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "d":
            return anchor - timedelta(days=n)
        if unit == "w":
            return anchor - timedelta(weeks=n)
        if unit == "m":
            return _add_months(anchor, -n)
        if unit == "y":
            return _add_months(anchor, -12 * n)

    try:
        return date.fromisoformat(spec)
    except ValueError as exc:
        raise ValueError(
            f"invalid date spec: {spec!r}; "
            f"expected today/yesterday/tomorrow, "
            f"week/month/quarter/year, Nd|Nw|Nm|Ny, or YYYY-MM-DD"
        ) from exc


def parse_range(spec: str, *, anchor: date | None = None) -> tuple[date, date]:
    """Parse a range specifier into (start, end), inclusive.

    Accepted forms:

        today | yesterday | tomorrow      (single day)
        week | month | quarter | year     (start-of-period to anchor)
        Nd | Nw | Nm | Ny                 (anchor minus N units .. anchor)
        YYYY-MM-DD                         (single day)
        YYYY-MM-DD:YYYY-MM-DD              (explicit range)
    """
    anchor = anchor or _today()
    spec = spec.strip()

    if ":" in spec and not spec.startswith(":"):
        # ISO range form
        left, right = spec.split(":", 1)
        try:
            start = date.fromisoformat(left)
            end = date.fromisoformat(right)
        except ValueError as exc:
            raise ValueError(
                f"invalid range spec: {spec!r}; expected YYYY-MM-DD:YYYY-MM-DD"
            ) from exc
        if start > end:
            raise ValueError(f"start date {start} must not be after end date {end}")
        return start, end

    if spec in _RELATIVE_DAYS:
        d = anchor + timedelta(days=_RELATIVE_DAYS[spec])
        return d, d

    if spec in _NAMED_PERIODS:
        return parse_since(spec, anchor=anchor), anchor

    if _DURATION_RE.match(spec):
        return parse_since(spec, anchor=anchor), anchor

    try:
        d = date.fromisoformat(spec)
        return d, d
    except ValueError as exc:
        raise ValueError(
            f"unrecognised range spec: {spec!r}; "
            f"expected today/yesterday/tomorrow, "
            f"week/month/quarter/year, Nd|Nw|Nm|Ny, "
            f"YYYY-MM-DD, or YYYY-MM-DD:YYYY-MM-DD"
        ) from exc
