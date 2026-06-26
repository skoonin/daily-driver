"""Behavior test for the Ashby source's `ashby_boards` knob and field mapping.

`sources.ashby.ashby_boards` is the configurable list of board slugs; each slug
becomes one posting-api fetch. Pins that a non-default board list drives exactly
those per-board API URLs, and that one Ashby posting maps onto the standard
emitted job dict (with the `isListed`/role-filter skips applied).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import ashby as ashby_module


def _config(boards: list[str]) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": ["Engineer"],
                "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
                "sources": {"ashby": {"ashby_boards": boards}},
            }
        )
    )


def _response(jobs: list[dict[str, Any]]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"jobs": jobs, "apiVersion": "1"}
    return resp


def test_ashby_boards_drive_fetched_api_urls(monkeypatch: Any) -> None:
    fetched: list[str] = []

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        fetched.append(url)
        return _response([])

    monkeypatch.setattr(ashby_module, "_api_get", fake_api_get)
    monkeypatch.setattr(ashby_module, "_http_session", lambda cfg: MagicMock())

    ashby_module.scrape_ashby(_config(["ramp", "linear"]))

    assert "https://api.ashbyhq.com/posting-api/job-board/ramp" in fetched
    assert "https://api.ashbyhq.com/posting-api/job-board/linear" in fetched


def test_ashby_maps_fields_and_applies_skips(monkeypatch: Any) -> None:
    board_jobs = [
        {
            "title": "Senior Cloud Engineer",
            "location": "United States",
            "jobUrl": "https://jobs.ashbyhq.com/two-words/abc",
            "descriptionPlain": "Build the platform.",
            "isListed": True,
        },
        # Skipped: title does not match the configured roles.
        {
            "title": "Director, Marketing",
            "location": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/two-words/def",
            "descriptionPlain": "Lead growth.",
            "isListed": True,
        },
        # Skipped: explicitly de-listed even though the title would match.
        {
            "title": "Staff Engineer",
            "location": "",
            "jobUrl": "https://jobs.ashbyhq.com/two-words/ghi",
            "descriptionPlain": "Hidden role.",
            "isListed": False,
        },
    ]

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        return _response(board_jobs)

    monkeypatch.setattr(ashby_module, "_api_get", fake_api_get)
    monkeypatch.setattr(ashby_module, "_http_session", lambda cfg: MagicMock())

    # Hyphenated slug exercises the slug -> company derivation.
    results = ashby_module.scrape_ashby(_config(["two-words"]))

    assert len(results) == 1
    job = results[0]
    assert job["company"] == "Two Words"  # derived from the slug
    assert job["role"] == "Senior Cloud Engineer"
    assert job["location"] == "United States"
    assert job["url"] == "https://jobs.ashbyhq.com/two-words/abc"
    assert job["source"] == "Ashby (two-words)"
    assert job["description_text"] == "Build the platform."


def test_ashby_empty_location_falls_back_to_remote(monkeypatch: Any) -> None:
    board_jobs = [
        {
            "title": "Platform Engineer",
            "location": "",
            "jobUrl": "https://jobs.ashbyhq.com/acme/xyz",
            "descriptionPlain": "",
            "isListed": True,
        }
    ]

    monkeypatch.setattr(
        ashby_module, "_api_get", lambda *a, **kw: _response(board_jobs)
    )
    monkeypatch.setattr(ashby_module, "_http_session", lambda cfg: MagicMock())

    results = ashby_module.scrape_ashby(_config(["acme"]))

    assert results[0]["location"] == "Remote"
