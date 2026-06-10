"""Tests for the RemoteOK JSON API scraper source."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import remoteok as remoteok_module


def _config(roles: list[str] | None = None) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": roles if roles is not None else ["Engineer", "SRE"],
                "scraper": {
                    "enabled": True,
                    "timeout": 1,
                    "max_retries": 0,
                },
            }
        )
    )


def _api_response(payload: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_float_string_salary_does_not_fail_source(monkeypatch: Any) -> None:
    """A row with a non-integer salary must not abort the whole source.

    RemoteOK can return floats or float-strings for salary. A bare int()
    coercion raises ValueError and (via the runner's broad except) drops
    every row from the source. The tolerant coercion must keep the clean
    rows and still surface the odd row with its comp formatted.
    """
    payload = [
        {
            "id": "1",
            "position": "Site Reliability Engineer",
            "company": "Acme",
            "url": "https://remoteok.com/1",
            "salary_min": "120000.5",
            "salary_max": "150000.9",
            "salary_currency": "USD",
        },
        {
            "id": "2",
            "position": "Staff Engineer",
            "company": "Globex",
            "url": "https://remoteok.com/2",
            "salary_min": 130000,
            "salary_max": 160000,
            "salary_currency": "USD",
        },
    ]
    monkeypatch.setattr(
        remoteok_module, "_api_get", lambda *a, **kw: _api_response(payload)
    )
    monkeypatch.setattr(remoteok_module, "_http_session", lambda cfg: MagicMock())

    jobs = remoteok_module.scrape_remoteok(_config())

    companies = {j["company"] for j in jobs}
    assert companies == {"Acme", "Globex"}

    by_company = {j["company"]: j for j in jobs}
    # Clean integer row formats normally.
    assert by_company["Globex"]["comp"] == "$130,000-$160,000/yr"
    # Float-string row is coerced (truncated via int(float(...))), not dropped.
    assert by_company["Acme"]["comp"] == "$120,000-$150,000/yr"
