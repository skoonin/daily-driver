"""Tests for `_api_get` retry behavior on 429/503 (rate-limit handling)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import requests

from daily_driver.scraper.sources._http import _api_get


def _fake_response(status: int, headers: dict[str, str] | None = None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = headers or {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status} Client Error", response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _config_with_timeout(max_retries: int = 3) -> dict[str, Any]:
    return {"job_search": {"scraper": {"timeout": 1, "max_retries": max_retries}}}


def test_max_retries_from_config_applies_when_not_overridden() -> None:
    """Config-supplied max_retries determines how many retries _api_get does."""
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _fake_response(429)
    sleeps: list[float] = []

    resp = _api_get(
        session,
        "https://x",
        _config_with_timeout(max_retries=1),
        label="t",
        sleep=sleeps.append,
    )

    assert resp is None
    assert session.get.call_count == 2
    assert len(sleeps) == 1


def test_explicit_max_retries_overrides_config() -> None:
    """Explicit max_retries kwarg overrides the config value."""
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _fake_response(429)
    sleeps: list[float] = []

    resp = _api_get(
        session,
        "https://x",
        _config_with_timeout(max_retries=5),
        label="t",
        max_retries=0,
        sleep=sleeps.append,
    )

    assert resp is None
    assert session.get.call_count == 1
    assert sleeps == []


def test_succeeds_first_attempt() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _fake_response(200)

    sleeps: list[float] = []
    resp = _api_get(
        session, "https://x", _config_with_timeout(), label="t", sleep=sleeps.append
    )

    assert resp is not None
    assert sleeps == []
    assert session.get.call_count == 1


def test_retries_on_429_then_succeeds() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = [
        _fake_response(429),
        _fake_response(429),
        _fake_response(200),
    ]
    sleeps: list[float] = []

    resp = _api_get(
        session, "https://x", _config_with_timeout(), label="t", sleep=sleeps.append
    )

    assert resp is not None
    assert session.get.call_count == 3
    assert len(sleeps) == 2
    assert sleeps[0] < sleeps[1]


def test_honors_retry_after_header() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = [
        _fake_response(429, headers={"Retry-After": "7"}),
        _fake_response(200),
    ]
    sleeps: list[float] = []

    _api_get(
        session, "https://x", _config_with_timeout(), label="t", sleep=sleeps.append
    )

    assert sleeps == [7.0]


def test_invalid_retry_after_falls_back_to_backoff() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = [
        _fake_response(429, headers={"Retry-After": "garbage"}),
        _fake_response(200),
    ]
    sleeps: list[float] = []

    _api_get(
        session, "https://x", _config_with_timeout(), label="t", sleep=sleeps.append
    )

    assert sleeps and sleeps[0] > 0
    assert sleeps[0] != 7.0


def test_retries_on_503() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = [_fake_response(503), _fake_response(200)]
    sleeps: list[float] = []

    resp = _api_get(
        session, "https://x", _config_with_timeout(), label="t", sleep=sleeps.append
    )

    assert resp is not None
    assert session.get.call_count == 2


def test_does_not_retry_on_404() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _fake_response(404)
    sleeps: list[float] = []

    resp = _api_get(
        session, "https://x", _config_with_timeout(), label="t", sleep=sleeps.append
    )

    assert resp is None
    assert session.get.call_count == 1
    assert sleeps == []


def test_gives_up_after_max_retries() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _fake_response(429)
    sleeps: list[float] = []

    resp = _api_get(
        session,
        "https://x",
        _config_with_timeout(),
        label="t",
        max_retries=2,
        sleep=sleeps.append,
    )

    assert resp is None
    assert session.get.call_count == 3
    assert len(sleeps) == 2


def test_retries_on_connection_error() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = [
        requests.ConnectionError("boom"),
        _fake_response(200),
    ]
    sleeps: list[float] = []

    resp = _api_get(
        session, "https://x", _config_with_timeout(), label="t", sleep=sleeps.append
    )

    assert resp is not None
    assert session.get.call_count == 2


def test_backoff_is_capped() -> None:
    """Backoff must not exceed _MAX_BACKOFF_SECONDS even with many attempts."""
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _fake_response(429)
    sleeps: list[float] = []

    _api_get(
        session,
        "https://x",
        _config_with_timeout(),
        label="t",
        max_retries=10,
        sleep=sleeps.append,
    )

    from daily_driver.scraper.sources._http import _MAX_BACKOFF_SECONDS

    assert all(s <= _MAX_BACKOFF_SECONDS for s in sleeps)
