"""Behavior tests for the Lever source's `lever_boards` knob and field mapping.

`sources.lever.lever_boards` is the configurable list of board slugs; each slug
becomes one postings-api fetch (a bare JSON array, verified live 2026-07-04).
Pins that a non-default board list drives exactly those per-board API URLs, and
that one Lever posting maps onto the standard emitted job dict (role filter,
description composition, remote signal).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import (
    PartialSourceError,
    ScrapeContext,
)
from daily_driver.plugins.job_search.scraper.sources import lever as lever_module


def _config(boards: list[str]) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": ["Engineer"],
                "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
                "sources": {"lever": {"lever_boards": boards}},
            }
        )
    )


def _response(jobs: list[dict[str, Any]] | Any) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = jobs
    return resp


def _posting(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "text": "Senior Site Reliability Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "categories": {"location": "Vancouver, British Columbia, Canada"},
        "workplaceType": "on-site",
        "descriptionPlain": "Build the platform.",
        "lists": [],
        "additionalPlain": "",
    }
    base.update(overrides)
    return base


def test_lever_boards_drive_fetched_api_urls(monkeypatch: Any) -> None:
    fetched: list[str] = []

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        fetched.append(url)
        return _response([])

    monkeypatch.setattr(lever_module, "_api_get", fake_api_get)
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    lever_module.scrape_lever(_config(["15five", "1inch"]))

    assert "https://api.lever.co/v0/postings/15five?mode=json" in fetched
    assert "https://api.lever.co/v0/postings/1inch?mode=json" in fetched


def test_lever_maps_fields_and_applies_role_filter(monkeypatch: Any) -> None:
    board_jobs = [
        _posting(),
        # Skipped: title does not match the configured roles.
        _posting(text="Director, Marketing", hostedUrl="https://jobs.lever.co/x/2"),
    ]

    monkeypatch.setattr(
        lever_module, "_api_get", lambda *a, **kw: _response(board_jobs)
    )
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    # Hyphenated slug exercises the slug -> company derivation.
    results = lever_module.scrape_lever(_config(["two-words"]))

    assert len(results) == 1
    job = results[0]
    assert job["company"] == "Two Words"  # derived from the slug
    assert job["role"] == "Senior Site Reliability Engineer"
    assert job["location"] == "Vancouver, British Columbia, Canada"
    assert job["url"] == "https://jobs.lever.co/acme/abc-123"
    assert job["source"] == "Lever (two-words)"
    assert job["description_text"] == "Build the platform."


def test_lever_description_composes_lists_and_additional(monkeypatch: Any) -> None:
    """descriptionPlain carries only opening+body; the requirements live in
    `lists` (HTML content) and the closing blurb in additionalPlain. All three
    must reach description_text -- the fit pass needs the lists most."""
    board_jobs = [
        _posting(
            descriptionPlain="Intro and body.",
            lists=[
                {"text": "Requirements", "content": "<li>5 years <b>SRE</b></li>"},
                {"text": "", "content": ""},  # empty section contributes nothing
            ],
            additionalPlain="Benefits blurb.",
        )
    ]

    monkeypatch.setattr(
        lever_module, "_api_get", lambda *a, **kw: _response(board_jobs)
    )
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    results = lever_module.scrape_lever(_config(["acme"]))

    assert results[0]["description_text"] == (
        "Intro and body.\n\nRequirements\n5 years SRE\n\nBenefits blurb."
    )


def test_lever_remote_workplace_type_surfaces_in_location(monkeypatch: Any) -> None:
    """workplaceType=remote with a bare country location must say Remote in the
    location string -- the location filter is the only filter that drops jobs,
    and a city-mapped country would otherwise drop a remote role."""
    board_jobs = [
        _posting(categories={"location": "US"}, workplaceType="remote"),
        # Already says remote: no double annotation.
        _posting(
            hostedUrl="https://jobs.lever.co/acme/2",
            categories={"location": "US, Remote"},
            workplaceType="remote",
        ),
        # No location at all falls back to the plain Remote marker.
        _posting(
            hostedUrl="https://jobs.lever.co/acme/3",
            categories={},
            workplaceType="remote",
        ),
    ]

    monkeypatch.setattr(
        lever_module, "_api_get", lambda *a, **kw: _response(board_jobs)
    )
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    results = lever_module.scrape_lever(_config(["acme"]))

    assert [j["location"] for j in results] == ["US (Remote)", "US, Remote", "Remote"]


def test_lever_records_raw_enumeration_pre_role_filter(monkeypatch: Any) -> None:
    """Board-diff closure needs the COMPLETE listing, including postings the
    role filter drops; an empty board records an empty (still valid) set."""
    board_jobs = [
        _posting(),
        _posting(text="Director, Marketing", hostedUrl="https://jobs.lever.co/x/2"),
    ]

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        return _response(board_jobs if "/acme" in url else [])

    monkeypatch.setattr(lever_module, "_api_get", fake_api_get)
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    ctx = _config(["acme", "empty-co"])
    lever_module.scrape_lever(ctx)

    assert ctx.enumerations["Lever (acme)"] == {
        "https://jobs.lever.co/acme/abc-123",
        "https://jobs.lever.co/x/2",
    }
    assert ctx.enumerations["Lever (empty-co)"] == set()


def test_lever_failed_board_raises_degraded_keeping_data(monkeypatch: Any) -> None:
    """A board whose request fails raises PartialSourceError carrying the jobs
    matched from the boards that succeeded, so a partial/all-failed scrape is
    recorded as degraded -- not a clean "0 found" indistinguishable from no match.
    A failed board must also record NO enumeration (closure safety)."""

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        # "ok" returns a posting; "down" fails (falsy response).
        return _response([_posting()]) if "/ok" in url else None

    monkeypatch.setattr(lever_module, "_api_get", fake_api_get)
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    ctx = _config(["ok", "down"])
    with pytest.raises(PartialSourceError) as excinfo:
        lever_module.scrape_lever(ctx)

    assert len(excinfo.value.jobs) == 1
    assert "down" in excinfo.value.reason
    assert "Lever (down)" not in ctx.enumerations


def test_lever_non_list_body_marks_board_failed(monkeypatch: Any) -> None:
    """The endpoint contract is a bare array; an object body (e.g. an error
    payload served with HTTP 200) is a broken fetch, never an enumeration."""
    monkeypatch.setattr(
        lever_module,
        "_api_get",
        lambda *a, **kw: _response({"ok": False, "error": "Document not found"}),
    )
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    ctx = _config(["weird"])
    with pytest.raises(PartialSourceError) as excinfo:
        lever_module.scrape_lever(ctx)

    assert excinfo.value.jobs == []
    assert "weird" in excinfo.value.reason
    assert ctx.enumerations == {}


def test_lever_all_boards_failed_raises_degraded_empty(monkeypatch: Any) -> None:
    monkeypatch.setattr(lever_module, "_api_get", lambda *a, **kw: None)
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    with pytest.raises(PartialSourceError) as excinfo:
        lever_module.scrape_lever(_config(["a", "b"]))

    assert excinfo.value.jobs == []
    assert "2 of 2" in excinfo.value.reason


def test_discovered_boards_unioned_with_pins(monkeypatch: Any) -> None:
    """The matched cache on ctx extends the pinned config boards; the
    exclude_boards blocklist trumps both."""
    from dataclasses import replace

    fetched: list[str] = []

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        fetched.append(url)
        return _response([])

    monkeypatch.setattr(lever_module, "_api_get", fake_api_get)
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    plugin = JobSearchPlugin.model_validate(
        {
            "roles": ["Engineer"],
            "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
            "sources": {
                "lever": {"lever_boards": ["15five"], "exclude_boards": ["noisy-co"]}
            },
        }
    )
    ctx = replace(
        ScrapeContext(plugin=plugin),
        discovered_boards={"lever": ("1inch", "noisy-co")},
    )

    lever_module.scrape_lever(ctx)

    slugs = [u.rsplit("/", 1)[1].removesuffix("?mode=json") for u in fetched]
    assert slugs == ["15five", "1inch"]
