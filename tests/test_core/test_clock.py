from __future__ import annotations

from datetime import date, datetime, timezone

import daily_driver.core.clock as clock_mod
from daily_driver.core.clock import now, today


def test_now_returns_aware_datetime() -> None:
    result = now()
    assert result.tzinfo is not None


def test_today_returns_date() -> None:
    result = today()
    assert isinstance(result, date)
    assert not isinstance(result, datetime)


def test_frozen_time_now() -> None:
    frozen = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen
    try:
        assert now() == frozen
        assert today() == frozen.date()
    finally:
        clock_mod.FROZEN_TIME = None


def test_frozen_time_restored() -> None:
    clock_mod.FROZEN_TIME = None
    result = now()
    assert result.tzinfo is not None
