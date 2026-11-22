"""Durations written as text."""

from __future__ import annotations

from datetime import timedelta

import pytest

from daily_driver.durations import parse_duration


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("24h", timedelta(hours=24)),
        ("1d", timedelta(days=1)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        (" 2D ", timedelta(days=2)),
    ],
)
def test_parse_duration_reads_days_hours_and_minutes(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text", ["", "12", "h", "30m1h", "1.5h", "-1h", "1w", "99999999999d"]
)
def test_parse_duration_rejects_text_without_a_known_unit(text):
    with pytest.raises(ValueError, match="invalid duration"):
        parse_duration(text)
