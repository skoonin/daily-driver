"""Behavior test for the Greenhouse source's `greenhouse_boards` knob.

`sources.greenhouse.greenhouse_boards` is the configurable list of board slugs;
each slug becomes one boards-api fetch. Pins that a non-default board list
drives exactly those per-board API URLs.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import greenhouse as gh_module


def _config(boards: list[str]) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": ["Engineer"],
                "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
                "sources": {"greenhouse": {"greenhouse_boards": boards}},
            }
        )
    )


def _empty_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"jobs": []}
    return resp


def test_greenhouse_boards_drive_fetched_api_urls(monkeypatch: Any) -> None:
    fetched: list[str] = []

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        fetched.append(url)
        return _empty_response()

    monkeypatch.setattr(gh_module, "_api_get", fake_api_get)
    monkeypatch.setattr(gh_module, "_http_session", lambda cfg: MagicMock())

    gh_module.scrape_greenhouse(_config(["stripe", "datadog"]))

    assert (
        "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true" in fetched
    )
    assert (
        "https://boards-api.greenhouse.io/v1/boards/datadog/jobs?content=true"
        in fetched
    )
    # The default ["anthropic"] board is not fetched when overridden.
    assert not any("anthropic" in u for u in fetched)
