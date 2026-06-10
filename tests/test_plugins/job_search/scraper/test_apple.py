"""Tests for the Apple Careers Playwright scraper source."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import apple as apple_module

_FIXTURE = Path(__file__).parents[3] / "fixtures" / "scraper" / "apple" / "sample.json"


@pytest.fixture(autouse=True)
def _stub_postlocation(monkeypatch: Any) -> None:
    """Resolve every country to a fixed postLocation code so the fake-page flow
    runs. The real resolver reads page.request.get(...).json(), which a bare
    MagicMock can't satisfy. Tests that need a different resolution re-patch it.
    """
    monkeypatch.setattr(
        apple_module, "apple_postlocation_code", lambda name, fetch_json: "USA"
    )


def _config(roles: list[str] | None = None) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": roles if roles is not None else ["Engineer", "SRE"],
                "scraper": {
                    "enabled": True,
                    "timeout": 5,
                    "max_retries": 0,
                    "headless": True,
                    "max_pages": 1,
                },
                "locations": {
                    "countries": ["US"],
                },
            }
        )
    )


def _make_fake_page(api_payload: dict[str, Any]) -> MagicMock:
    """Return a mock Playwright page modeling the event-driven wait pattern.

    The scraper now drives the search via ``page.expect_response(...)``: it
    enters the context manager, runs the triggering action (Enter / scroll),
    then the SPA fires a /api/v1/search response. This fake fires the captured
    response listener when the action runs *inside* an active expect_response
    block, matching the real ordering.
    """
    page = MagicMock()
    captured_callbacks: list[Any] = []
    fired = {"count": 0}

    def fake_on(event: str, callback: Any) -> None:
        if event == "response":
            captured_callbacks.append(callback)

    def fake_remove_listener(event: str, callback: Any) -> None:
        pass

    def _fire_response() -> None:
        mock_response = MagicMock()
        mock_response.url = "https://jobs.apple.com/api/v1/search"
        mock_response.json.return_value = api_payload
        for cb in captured_callbacks:
            cb(mock_response)
        fired["count"] += 1

    @contextmanager
    def fake_expect_response(predicate: Any, **kwargs: Any) -> Any:
        # The scraper runs the triggering action inside this block; a real
        # /api/v1/search POST fires while we're waiting. Fire on first scroll/
        # Enter pass only (one API "page" of results), so the result-count
        # stall check ends pagination just like the live SPA.
        if fired["count"] == 0:
            _fire_response()
        yield MagicMock()

    search_input = MagicMock()
    search_input.fill = MagicMock()
    page.query_selector.return_value = search_input
    page.on.side_effect = fake_on
    page.remove_listener.side_effect = fake_remove_listener
    page.goto = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.evaluate = MagicMock()
    page.press = MagicMock()
    page.expect_response.side_effect = fake_expect_response

    return page


@contextmanager
def _fake_browser(page: MagicMock) -> Generator[MagicMock, None, None]:
    yield page


def test_parses_sample_listing(monkeypatch: Any) -> None:
    payload = json.loads(_FIXTURE.read_text())
    page = _make_fake_page(payload)

    monkeypatch.setattr(
        apple_module, "_playwright_browser", lambda cfg: _fake_browser(page)
    )

    jobs = apple_module.scrape_apple(_config())

    roles = {j["role"] for j in jobs}
    assert "Senior Site Reliability Engineer" in roles
    assert "Staff Software Engineer" in roles

    for job in jobs:
        assert job["company"] == "Apple"
        assert job["source"] == "Apple Careers"
        assert "jobs.apple.com" in job["url"]


def test_handles_empty_results(monkeypatch: Any) -> None:
    empty_payload: dict[str, Any] = {"res": {"searchResults": []}}
    page = _make_fake_page(empty_payload)

    monkeypatch.setattr(
        apple_module, "_playwright_browser", lambda cfg: _fake_browser(page)
    )

    jobs = apple_module.scrape_apple(_config())
    assert jobs == []


def test_returns_empty_when_playwright_unavailable(monkeypatch: Any) -> None:
    """Browser session failure must be swallowed — scrape returns empty list."""

    @contextmanager
    def _raise_browser(cfg: Any) -> Generator[None, None, None]:
        raise ImportError("playwright not installed")
        yield  # unreachable — keeps type checker happy

    monkeypatch.setattr(apple_module, "_playwright_browser", _raise_browser)

    jobs = apple_module.scrape_apple(_config())
    assert jobs == []


def test_country_resolving_to_none_is_skipped(monkeypatch: Any) -> None:
    """A country with no postLocation code is skipped without navigating."""
    payload = json.loads(_FIXTURE.read_text())
    page = _make_fake_page(payload)
    monkeypatch.setattr(
        apple_module, "_playwright_browser", lambda cfg: _fake_browser(page)
    )
    monkeypatch.setattr(apple_module, "apple_postlocation_code", lambda *a, **k: None)

    jobs = apple_module.scrape_apple(_config())
    assert jobs == []
    page.goto.assert_not_called()


def test_resolved_code_drives_location_query(monkeypatch: Any) -> None:
    """The resolved postLocation code scopes the navigation URL."""
    payload = json.loads(_FIXTURE.read_text())
    page = _make_fake_page(payload)
    monkeypatch.setattr(
        apple_module, "_playwright_browser", lambda cfg: _fake_browser(page)
    )
    monkeypatch.setattr(
        apple_module, "apple_postlocation_code", lambda name, fetch_json: "CHEC"
    )

    apple_module.scrape_apple(_config())

    goto_urls = [call.args[0] for call in page.goto.call_args_list]
    assert any("en-us/search?location=x-CHEC" in u for u in goto_urls)


def test_emits_country_in_location(monkeypatch: Any) -> None:
    """countryName must be joined into the emitted location string."""
    payload: dict[str, Any] = {
        "res": {
            "searchResults": [
                {
                    "postingTitle": "Senior Site Reliability Engineer",
                    "positionId": "200111",
                    "id": "200111",
                    "transformedPostingTitle": "senior-sre",
                    "locations": [
                        {
                            "name": "Seattle",
                            "stateProvince": "",
                            "countryName": "United States of America",
                        }
                    ],
                }
            ]
        }
    }
    page = _make_fake_page(payload)
    monkeypatch.setattr(
        apple_module, "_playwright_browser", lambda cfg: _fake_browser(page)
    )

    jobs = apple_module.scrape_apple(_config())
    assert len(jobs) == 1
    assert jobs[0]["location"] == "Seattle, United States of America"


def test_location_falls_back_to_various_when_locations_empty(
    monkeypatch: Any,
) -> None:
    """Empty locations[] preserves the 'Various' fallback."""
    payload: dict[str, Any] = {
        "res": {
            "searchResults": [
                {
                    "postingTitle": "Senior Site Reliability Engineer",
                    "positionId": "200222",
                    "id": "200222",
                    "transformedPostingTitle": "senior-sre",
                    "locations": [],
                }
            ]
        }
    }
    page = _make_fake_page(payload)
    monkeypatch.setattr(
        apple_module, "_playwright_browser", lambda cfg: _fake_browser(page)
    )

    jobs = apple_module.scrape_apple(_config())
    assert len(jobs) == 1
    assert jobs[0]["location"] == "Various"


def test_expect_response_timeout_degrades_gracefully(monkeypatch: Any) -> None:
    """A timeout on expect_response (SPA never fired /api/v1/search) must not
    raise — the scrape continues and returns whatever was captured (here none)."""
    from playwright.sync_api import TimeoutError as PWTimeoutError

    page = _make_fake_page({"res": {"searchResults": []}})

    @contextmanager
    def timing_out(predicate: Any, **kwargs: Any) -> Any:
        raise PWTimeoutError("Timeout waiting for response")
        yield  # unreachable

    page.expect_response.side_effect = timing_out

    monkeypatch.setattr(
        apple_module, "_playwright_browser", lambda cfg: _fake_browser(page)
    )

    jobs = apple_module.scrape_apple(_config())
    assert jobs == []


def test_skips_items_without_position_id(monkeypatch: Any) -> None:
    """Items missing positionId must not be returned."""
    payload: dict[str, Any] = {
        "res": {
            "searchResults": [
                {
                    "postingTitle": "Senior Engineer",
                    "positionId": "",
                    "id": "",
                    "transformedPostingTitle": "senior-engineer",
                    "locations": [{"name": "Cupertino"}],
                }
            ]
        }
    }
    page = _make_fake_page(payload)
    monkeypatch.setattr(
        apple_module, "_playwright_browser", lambda cfg: _fake_browser(page)
    )

    jobs = apple_module.scrape_apple(_config())
    assert jobs == []
