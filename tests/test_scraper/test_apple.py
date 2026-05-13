"""Tests for the Apple Careers Playwright scraper source."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from daily_driver.scraper.sources import apple as apple_module

_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "scraper" / "apple" / "sample.json"
)


def _config(roles: list[str] | None = None) -> dict[str, Any]:
    return {
        "job_search": {
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
    }


def _make_fake_page(api_payload: dict[str, Any]) -> MagicMock:
    """Return a mock Playwright page that fires the response listener once."""
    page = MagicMock()
    captured_callbacks: list[Any] = []

    def fake_on(event: str, callback: Any) -> None:
        if event == "response":
            captured_callbacks.append(callback)

    def fake_remove_listener(event: str, callback: Any) -> None:
        pass

    def fake_fill(value: str) -> None:
        # Only fire on actual search terms; fill("") is a clear-before-type call.
        if not value:
            return
        mock_response = MagicMock()
        mock_response.url = "https://jobs.apple.com/api/v1/search"
        mock_response.json.return_value = api_payload
        for cb in captured_callbacks:
            cb(mock_response)

    search_input = MagicMock()
    search_input.fill = fake_fill
    page.query_selector.return_value = search_input
    page.on.side_effect = fake_on
    page.remove_listener.side_effect = fake_remove_listener
    page.goto = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.evaluate = MagicMock()
    page.press = MagicMock()

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
