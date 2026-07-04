"""Behavior test for the Workable source's `workable_accounts` knob and mapping.

`sources.workable.workable_accounts` is the configurable list of account slugs;
each slug becomes one widget-API fetch. Pins that a non-default account list
drives exactly those per-account API URLs, and that one Workable posting maps
onto the standard emitted job dict (with the role-filter skip applied). Unlike
Ashby, the company name comes from the payload's top-level `name`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import workable as workable_module


def _config(accounts: list[str]) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": ["Engineer"],
                "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
                "sources": {"workable": {"workable_accounts": accounts}},
            }
        )
    )


def _response(name: str | None, jobs: list[dict[str, Any]]) -> MagicMock:
    resp = MagicMock()
    payload: dict[str, Any] = {"jobs": jobs, "description": "..."}
    if name is not None:
        payload["name"] = name
    resp.json.return_value = payload
    return resp


def test_workable_accounts_drive_fetched_api_urls(monkeypatch: Any) -> None:
    fetched: list[str] = []

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        fetched.append(url)
        return _response("Acme", [])

    monkeypatch.setattr(workable_module, "_api_get", fake_api_get)
    monkeypatch.setattr(workable_module, "_http_session", lambda cfg: MagicMock())

    workable_module.scrape_workable(_config(["huggingface", "acme"]))

    assert "https://apply.workable.com/api/v1/widget/accounts/huggingface" in fetched
    assert "https://apply.workable.com/api/v1/widget/accounts/acme" in fetched


def test_workable_maps_fields_and_applies_role_skip(monkeypatch: Any) -> None:
    account_jobs = [
        {
            "title": "Senior Cloud Engineer",
            "city": "Paris",
            "country": "France",
            "url": "https://apply.workable.com/j/ABC123",
        },
        # Skipped: title does not match the configured roles.
        {
            "title": "Director, Marketing",
            "city": "Remote",
            "country": "",
            "url": "https://apply.workable.com/j/DEF456",
        },
    ]

    monkeypatch.setattr(
        workable_module,
        "_api_get",
        lambda *a, **kw: _response("Hugging Face", account_jobs),
    )
    monkeypatch.setattr(workable_module, "_http_session", lambda cfg: MagicMock())

    results = workable_module.scrape_workable(_config(["huggingface"]))

    assert len(results) == 1
    job = results[0]
    # Company comes from the payload's top-level `name`, not the slug.
    assert job["company"] == "Hugging Face"
    assert job["role"] == "Senior Cloud Engineer"
    assert job["location"] == "Paris, France"
    assert job["url"] == "https://apply.workable.com/j/ABC123"
    assert job["source"] == "Workable (huggingface)"
    assert job["description_text"] == ""


def test_workable_company_falls_back_to_slug(monkeypatch: Any) -> None:
    account_jobs = [
        {
            "title": "Platform Engineer",
            "city": "",
            "country": "",
            "url": "https://apply.workable.com/j/XYZ",
        }
    ]

    # No top-level `name` in the payload exercises the slug -> company fallback.
    monkeypatch.setattr(
        workable_module, "_api_get", lambda *a, **kw: _response(None, account_jobs)
    )
    monkeypatch.setattr(workable_module, "_http_session", lambda cfg: MagicMock())

    results = workable_module.scrape_workable(_config(["two-words"]))

    assert results[0]["company"] == "Two Words"
    # Empty city/country falls back to Remote.
    assert results[0]["location"] == "Remote"


def test_workable_failed_account_raises_degraded_keeping_data(
    monkeypatch: Any,
) -> None:
    """An account whose request fails raises PartialSourceError carrying the jobs
    from the accounts that succeeded, so a partial scrape is recorded as degraded
    rather than a clean "0 found" indistinguishable from no match."""
    import pytest

    from daily_driver.plugins.job_search.scraper.context import PartialSourceError

    ok_jobs = [
        {
            "title": "Platform Engineer",
            "city": "Remote",
            "country": "",
            "url": "https://apply.workable.com/j/OK",
        }
    ]

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        # "ok" returns a posting; "down" fails (falsy response).
        return _response("OK Co", ok_jobs) if url.endswith("/ok") else None

    monkeypatch.setattr(workable_module, "_api_get", fake_api_get)
    monkeypatch.setattr(workable_module, "_http_session", lambda cfg: MagicMock())

    with pytest.raises(PartialSourceError) as excinfo:
        workable_module.scrape_workable(_config(["ok", "down"]))

    assert len(excinfo.value.jobs) == 1
    assert "down" in excinfo.value.reason


def test_workable_all_accounts_failed_raises_degraded_empty(
    monkeypatch: Any,
) -> None:
    """Every account failing yields an empty job list AND a degraded signal, so
    the all-failed case is never read as a clean, complete scrape."""
    import pytest

    from daily_driver.plugins.job_search.scraper.context import PartialSourceError

    monkeypatch.setattr(workable_module, "_api_get", lambda *a, **kw: None)
    monkeypatch.setattr(workable_module, "_http_session", lambda cfg: MagicMock())

    with pytest.raises(PartialSourceError) as excinfo:
        workable_module.scrape_workable(_config(["a", "b"]))

    assert excinfo.value.jobs == []
    assert "2 of 2" in excinfo.value.reason
