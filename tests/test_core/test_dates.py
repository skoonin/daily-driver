from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from daily_driver.core import clock
from daily_driver.core.dates import parse_range, parse_since

# Pinned anchor: Monday, 2026-04-20.
_FROZEN = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)
_ANCHOR = date(2026, 4, 20)


@pytest.fixture(autouse=True)
def _freeze_clock(monkeypatch):
    monkeypatch.setattr(clock, "FROZEN_TIME", _FROZEN)
    yield


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("today", date(2026, 4, 20)),
        ("yesterday", date(2026, 4, 19)),
        ("tomorrow", date(2026, 4, 21)),
        ("week", date(2026, 4, 20)),  # Monday
        ("month", date(2026, 4, 1)),
        ("quarter", date(2026, 4, 1)),
        ("year", date(2026, 1, 1)),
        ("1d", date(2026, 4, 19)),
        ("7d", date(2026, 4, 13)),
        ("2w", date(2026, 4, 6)),
        ("1m", date(2026, 3, 20)),
        ("1y", date(2025, 4, 20)),
        ("2026-01-15", date(2026, 1, 15)),
    ],
)
def test_parse_since(spec, expected):
    assert parse_since(spec) == expected


def test_parse_since_invalid():
    with pytest.raises(ValueError):
        parse_since("garbage")


# ---------------------------------------------------------------------------
# parse_range
# ---------------------------------------------------------------------------


def test_parse_range_today():
    assert parse_range("today") == (_ANCHOR, _ANCHOR)


def test_parse_range_yesterday():
    y = date(2026, 4, 19)
    assert parse_range("yesterday") == (y, y)


def test_parse_range_week():
    # Anchor is Monday → week start is anchor itself, end is anchor.
    assert parse_range("week") == (_ANCHOR, _ANCHOR)


def test_parse_range_month():
    assert parse_range("month") == (date(2026, 4, 1), _ANCHOR)


def test_parse_range_quarter():
    assert parse_range("quarter") == (date(2026, 4, 1), _ANCHOR)


def test_parse_range_year():
    assert parse_range("year") == (date(2026, 1, 1), _ANCHOR)


def test_parse_range_relative_duration():
    assert parse_range("7d") == (date(2026, 4, 13), _ANCHOR)
    assert parse_range("2w") == (date(2026, 4, 6), _ANCHOR)
    assert parse_range("1m") == (date(2026, 3, 20), _ANCHOR)


def test_parse_range_iso_single():
    assert parse_range("2026-03-15") == (date(2026, 3, 15), date(2026, 3, 15))


def test_parse_range_iso_range():
    s, e = date(2026, 3, 1), date(2026, 3, 31)
    assert parse_range("2026-03-01:2026-03-31") == (s, e)


def test_parse_range_iso_range_inverted_rejected():
    with pytest.raises(ValueError, match="must not be after"):
        parse_range("2026-04-30:2026-04-01")


def test_parse_range_invalid():
    with pytest.raises(ValueError):
        parse_range("nonsense")


# ---------------------------------------------------------------------------
# Month-rollover edge: anchor on Jan 31, "1m" must clamp.
# ---------------------------------------------------------------------------


def test_parse_since_month_clamps_to_last_day(monkeypatch):
    feb_anchor = date(2026, 1, 31)
    assert parse_since("1m", anchor=feb_anchor) == date(2025, 12, 31)
    # 2025-02-28 because 2025 isn't a leap year and Jan 31 - 1m clamps
    assert parse_since("11m", anchor=feb_anchor) == date(2025, 2, 28)
