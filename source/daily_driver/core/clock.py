"""Date/time utilities — single source of truth for all time operations.

All datetimes produced here are timezone-aware. Never use naive datetimes
in the application — the FROZEN_TIME mechanism exists so tests get
deterministic results without monkey-patching.
"""

from __future__ import annotations

from datetime import date, datetime

# Not thread-safe: tests that set this must not run in parallel (pytest -n auto / xdist).
FROZEN_TIME: datetime | None = None


def now() -> datetime:
    """Return the current timezone-aware datetime (or FROZEN_TIME if set)."""
    if FROZEN_TIME is not None:
        return FROZEN_TIME
    return datetime.now().astimezone()


def today() -> date:
    """Return today's date (or the date component of FROZEN_TIME if set)."""
    return now().date()
