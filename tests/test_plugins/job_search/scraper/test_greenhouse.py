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


def _job_response(jobs: list[dict[str, Any]]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"jobs": jobs}
    return resp


def test_greenhouse_failed_board_raises_degraded_keeping_data(
    monkeypatch: Any,
) -> None:
    """A board whose request fails raises PartialSourceError carrying the jobs
    matched from the boards that succeeded — a transient board 404 must never
    look identical to a clean, complete scrape. Prerequisite for board-diff
    closure: without this signal a failed board's rows would read as absent
    (closed) while the source reports healthy.
    """
    import pytest

    from daily_driver.plugins.job_search.scraper.context import PartialSourceError

    ok_jobs = [
        {
            "title": "Platform Engineer",
            "location": {"name": "Remote"},
            "absolute_url": "https://boards.greenhouse.io/ok/jobs/1",
            "content": "",
        }
    ]

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        return _job_response(ok_jobs) if "/ok/" in url else None

    monkeypatch.setattr(gh_module, "_api_get", fake_api_get)
    monkeypatch.setattr(gh_module, "_http_session", lambda cfg: MagicMock())

    with pytest.raises(PartialSourceError) as excinfo:
        gh_module.scrape_greenhouse(_config(["ok", "down"]))

    assert len(excinfo.value.jobs) == 1
    assert "down" in excinfo.value.reason


def test_greenhouse_all_boards_failed_raises_degraded_empty(
    monkeypatch: Any,
) -> None:
    import pytest

    from daily_driver.plugins.job_search.scraper.context import PartialSourceError

    monkeypatch.setattr(gh_module, "_api_get", lambda *a, **kw: None)
    monkeypatch.setattr(gh_module, "_http_session", lambda cfg: MagicMock())

    with pytest.raises(PartialSourceError) as excinfo:
        gh_module.scrape_greenhouse(_config(["a", "b"]))

    assert excinfo.value.jobs == []
    assert "2 of 2" in excinfo.value.reason


def test_discovered_boards_unioned_with_pins(monkeypatch: Any) -> None:
    """The matched cache on ctx extends the pinned config boards; the
    exclude_boards blocklist trumps both."""
    fetched: list[str] = []

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        fetched.append(url)
        return _empty_response()

    monkeypatch.setattr(gh_module, "_api_get", fake_api_get)
    monkeypatch.setattr(gh_module, "_http_session", lambda cfg: MagicMock())

    from dataclasses import replace

    plugin = JobSearchPlugin.model_validate(
        {
            "roles": ["Engineer"],
            "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
            "sources": {
                "greenhouse": {
                    "greenhouse_boards": ["stripe"],
                    "exclude_boards": ["noisy-co"],
                }
            },
        }
    )
    ctx = replace(
        ScrapeContext(plugin=plugin),
        discovered_boards={"greenhouse": ("movableink", "noisy-co")},
    )

    gh_module.scrape_greenhouse(ctx)

    assert [u.split("/boards/")[1].split("/")[0] for u in fetched] == [
        "stripe",
        "movableink",
    ]
