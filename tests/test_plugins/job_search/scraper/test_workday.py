"""Behavior test for the Workday source: pagination, field mapping, config.

`sources.workday.workday_boards` is a list of {tenant, host, site, company?}
objects. Unlike the single-GET board sources, Workday is a paginated POST API
(`{limit, offset}` body -> `{total, jobPostings[]}`), so these tests pin that
the source walks offsets until `total`, builds the right endpoint + job URLs,
maps a posting onto the standard emitted dict, and applies the role-filter skip.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import workday as workday_module


def _config(boards: list[dict[str, Any]]) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": ["Engineer"],
                "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
                "sources": {"workday": {"workday_boards": boards}},
            }
        )
    )


def _posting(title: str, path: str, location: str = "USA - Remote") -> dict[str, Any]:
    return {"title": title, "externalPath": path, "locationsText": location}


def _response(total: int, postings: list[dict[str, Any]]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"total": total, "jobPostings": postings}
    return resp


_BOARD = {"tenant": "crowdstrike", "host": "wd5", "site": "crowdstrikecareers"}


def test_workday_paginates_until_total(monkeypatch: Any) -> None:
    # 25 total across two pages of 20: offsets 0 and 20 are fetched, 40 is not.
    page0 = [_posting(f"Engineer {i}", f"/job/x/Engineer-{i}_R{i}") for i in range(20)]
    page1 = [
        _posting(f"Engineer {i}", f"/job/x/Engineer-{i}_R{i}") for i in range(20, 25)
    ]
    calls: list[tuple[str, int]] = []

    def fake_post(session: Any, url: str, ctx: Any, *, json: Any, **kw: Any) -> Any:
        calls.append((url, json["offset"]))
        return _response(25, page0 if json["offset"] == 0 else page1)

    monkeypatch.setattr(workday_module, "_api_post", fake_post)
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    results = workday_module.scrape_workday(_config([_BOARD]))

    api = "https://crowdstrike.wd5.myworkdayjobs.com/wday/cxs/crowdstrike/crowdstrikecareers/jobs"
    assert [c for c in calls] == [(api, 0), (api, 20)]
    assert len(results) == 25


def test_workday_maps_fields_and_builds_url(monkeypatch: Any) -> None:
    postings = [
        _posting("Senior Cloud Engineer", "/job/USA---Remote/Sr_R1", "USA - Remote"),
        # Skipped: title does not match the configured roles.
        _posting("Director, Marketing", "/job/x/Dir_R2"),
    ]

    monkeypatch.setattr(
        workday_module, "_api_post", lambda *a, **kw: _response(2, postings)
    )
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    results = workday_module.scrape_workday(
        _config([{**_BOARD, "company": "CrowdStrike"}])
    )

    assert len(results) == 1
    job = results[0]
    assert job["company"] == "CrowdStrike"  # explicit override wins
    assert job["role"] == "Senior Cloud Engineer"
    assert job["location"] == "USA - Remote"
    assert job["source"] == "Workday (crowdstrike)"
    assert job["description_text"] == ""
    assert (
        job["url"]
        == "https://crowdstrike.wd5.myworkdayjobs.com/en-US/crowdstrikecareers/job/USA---Remote/Sr_R1"
    )


def test_workday_company_defaults_to_tenant(monkeypatch: Any) -> None:
    postings = [_posting("Platform Engineer", "/job/x/Plat_R3", "")]

    monkeypatch.setattr(
        workday_module, "_api_post", lambda *a, **kw: _response(1, postings)
    )
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    # No `company` override exercises the tenant -> display-name derivation.
    results = workday_module.scrape_workday(
        _config([{"tenant": "acme-corp", "host": "wd1", "site": "careers"}])
    )

    assert results[0]["company"] == "Acme Corp"
    # Empty locationsText falls back to Remote.
    assert results[0]["location"] == "Remote"


def test_workday_stops_on_empty_page(monkeypatch: Any) -> None:
    # total claims more than the endpoint actually returns; an empty page ends it.
    def fake_post(session: Any, url: str, ctx: Any, *, json: Any, **kw: Any) -> Any:
        if json["offset"] == 0:
            return _response(999, [_posting("Engineer A", "/job/x/A_R1")])
        return _response(999, [])

    monkeypatch.setattr(workday_module, "_api_post", fake_post)
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    results = workday_module.scrape_workday(_config([_BOARD]))

    assert len(results) == 1


def test_workday_partial_fetch_failure_keeps_data_and_warns(
    monkeypatch: Any, caplog: Any
) -> None:
    """A mid-pagination HTTP failure keeps the fetched pages but logs PARTIAL,
    so an incomplete board is never reported as a clean, complete scrape."""
    page0 = [_posting(f"Engineer {i}", f"/job/x/E-{i}_R{i}") for i in range(20)]

    def fake_post(session: Any, url: str, ctx: Any, *, json: Any, **kw: Any) -> Any:
        # Page 1 (offset 20) of a 100-job board fails terminally.
        return _response(100, page0) if json["offset"] == 0 else None

    monkeypatch.setattr(workday_module, "_api_post", fake_post)
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    import logging

    with caplog.at_level(logging.WARNING):
        results = workday_module.scrape_workday(_config([_BOARD]))

    assert len(results) == 20  # the fetched page is kept, not discarded
    assert any("PARTIAL" in r.message for r in caplog.records)


def test_workday_missing_total_paginates_until_empty(monkeypatch: Any) -> None:
    """A response with no `total` must paginate to the empty-page terminator,
    not silently stop after page 1 (a defaulted total=0 would cap it there)."""
    page0 = [_posting(f"Engineer {i}", f"/job/x/E-{i}_R{i}") for i in range(20)]
    page1 = [_posting(f"Engineer {i}", f"/job/x/E-{i}_R{i}") for i in range(20, 25)]

    def fake_post(session: Any, url: str, ctx: Any, *, json: Any, **kw: Any) -> Any:
        resp = MagicMock()
        offset = json["offset"]
        # No `total` key in any response.
        page = page0 if offset == 0 else page1 if offset == 20 else []
        resp.json.return_value = {"jobPostings": page}
        return resp

    monkeypatch.setattr(workday_module, "_api_post", fake_post)
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    results = workday_module.scrape_workday(_config([_BOARD]))

    assert len(results) == 25  # both pages fetched despite the missing total
