"""Tests for `hn_jobs` source — YC-funded job stories via Algolia `tags=job`."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import hn_jobs as hn_jobs_module
from daily_driver.plugins.job_search.scraper.sources.hn_jobs import _parse_title


def _config(
    roles: list[str] | None = None,
    max_posts: int = 100,
    max_age_days: int = 0,
) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": roles or ["SRE", "Engineer"],
                "scraper": {
                    "enabled": True,
                    "timeout": 1,
                    "max_retries": 0,
                    "max_age_days": max_age_days,
                },
                "sources": {"hn_jobs": {"hn_max_posts": max_posts}},
            }
        )
    )


def _algolia_response(hits: list[dict[str, Any]]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"hits": hits, "nbHits": len(hits)}
    return resp


def test_parse_title_with_yc_batch_no_location_signal() -> None:
    """No location signal in title → defaults to 'Unknown' (not 'Remote')."""
    company, role, location = _parse_title("Mux (YC W16) Is Hiring")
    assert company == "Mux"
    assert role == ""
    assert location == "Unknown"


def test_parse_title_bare_in_phrase_does_not_match_location() -> None:
    """Bare 'in X' (no comma) is not treated as a location.

    Avoids polluting jobs.csv with technology terms like 'Distributed Systems',
    'Machine Learning', etc. The trade-off is that legitimate 'in Montreal' is
    also missed — accepted because false data is worse than missing data.
    """
    company, role, location = _parse_title(
        "GovernGPT (YC W24) Is Hiring Engineers to Build Thinking Systems in Montreal"
    )
    assert company == "GovernGPT"
    assert "Engineers" in role
    assert location == "Unknown"


def test_parse_title_does_not_misextract_technology_after_in() -> None:
    """'in Distributed Systems' / 'in Machine Learning' must NOT become locations."""
    for title in (
        "Acme (YC W22) Is Hiring Engineer to Work in Distributed Systems",
        "Acme (YC W22) Is Hiring Engineer Specializing in Machine Learning",
        "Acme (YC W22) Is Hiring Backend Engineer in Rust",
    ):
        _, _, location = _parse_title(title)
        assert location == "Unknown", title


def test_parse_title_extracts_city_region_with_comma() -> None:
    """'in San Francisco, CA' (comma-separated) IS extracted as location."""
    company, _, location = _parse_title(
        "Acme (YC W22) Is Hiring SRE in San Francisco, CA"
    )
    assert company == "Acme"
    assert location == "San Francisco, CA"


def test_parse_title_extracts_parenthesized_city() -> None:
    """Parenthesized suffix '(City)' at end of title IS extracted."""
    _, _, location = _parse_title("Acme (YC W22) Is Hiring SRE (Berlin)")
    assert location == "Berlin"


def test_parse_title_with_remote_marker() -> None:
    company, role, location = _parse_title(
        "Infisical (YC W23) Is Hiring Full Stack Software Engineers (Remote)"
    )
    assert company == "Infisical"
    assert "Full Stack" in role
    assert location == "Remote"


def test_parse_title_seeks_variant() -> None:
    company, role, _ = _parse_title(
        "Coverage Cat (YC S22) Seeks Fractional Engineer to Build AI Growth Toolkit"
    )
    assert company == "Coverage Cat"
    assert "Fractional Engineer" in role


def test_parse_title_no_yc_tag() -> None:
    company, role, _ = _parse_title(
        "Stardex Is Hiring a Founding Customer Success Lead"
    )
    assert company == "Stardex"
    assert "Founding Customer Success Lead" in role


def test_scrape_uses_search_by_date_and_job_tag(monkeypatch: Any) -> None:
    captured_urls: list[str] = []

    def fake_api_get(session: Any, url: str, config: Any, **kwargs: Any) -> MagicMock:
        captured_urls.append(url)
        return _algolia_response(
            [
                {
                    "objectID": "100",
                    "title": "Mux (YC W16) Is Hiring SRE",
                    "url": "https://www.mux.com/jobs",
                }
            ]
        )

    monkeypatch.setattr(hn_jobs_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_jobs_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_jobs_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_jobs_module.scrape_hn_jobs(_config())

    assert len(captured_urls) == 1
    assert "search_by_date" in captured_urls[0]
    assert "tags=job" in captured_urls[0]
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Mux"
    assert jobs[0]["url"] == "https://www.mux.com/jobs"
    assert jobs[0]["source"] == "HN Jobs"


def test_scrape_falls_back_to_hn_url_when_external_url_missing(
    monkeypatch: Any,
) -> None:
    """If the story has no external URL, fall back to the HN item URL."""

    def fake_api_get(*args: Any, **kwargs: Any) -> MagicMock:
        return _algolia_response(
            [
                {
                    "objectID": "999",
                    "title": "Acme (YC W22) Is Hiring Engineer",
                    "url": None,
                }
            ]
        )

    monkeypatch.setattr(hn_jobs_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_jobs_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_jobs_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_jobs_module.scrape_hn_jobs(_config())
    assert jobs[0]["url"] == "https://news.ycombinator.com/item?id=999"


def test_scrape_filters_by_roles(monkeypatch: Any) -> None:
    """Hits whose title doesn't match `matches_roles` are dropped."""

    def fake_api_get(*args: Any, **kwargs: Any) -> MagicMock:
        return _algolia_response(
            [
                {
                    "objectID": "1",
                    "title": "Acme (YC W22) Is Hiring SRE",
                    "url": "https://acme.example/jobs",
                },
                {
                    "objectID": "2",
                    "title": "Acme (YC W22) Is Hiring Dental Hygienist",
                    "url": "https://acme.example/jobs",
                },
            ]
        )

    monkeypatch.setattr(hn_jobs_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_jobs_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_jobs_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_jobs_module.scrape_hn_jobs(_config(roles=["SRE"]))
    assert len(jobs) == 1
    assert "SRE" in jobs[0]["role"]


def test_scrape_returns_empty_when_fetch_fails(monkeypatch: Any) -> None:
    monkeypatch.setattr(hn_jobs_module, "_api_get", lambda *a, **k: None)
    monkeypatch.setattr(hn_jobs_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_jobs_module, "today", lambda: date(2026, 5, 9))

    assert hn_jobs_module.scrape_hn_jobs(_config()) == []


def test_scrape_applies_max_age_days_to_algolia_query(monkeypatch: Any) -> None:
    """When max_age_days > 0, the Algolia URL must include numericFilters."""
    captured_urls: list[str] = []

    def fake_api_get(session: Any, url: str, config: Any, **kwargs: Any) -> MagicMock:
        captured_urls.append(url)
        return _algolia_response([])

    monkeypatch.setattr(hn_jobs_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_jobs_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_jobs_module, "today", lambda: date(2026, 5, 9))

    hn_jobs_module.scrape_hn_jobs(_config(max_age_days=30))
    assert any("numericFilters=created_at_i>" in u for u in captured_urls)

    captured_urls.clear()
    hn_jobs_module.scrape_hn_jobs(_config(max_age_days=0))
    assert not any("numericFilters" in u for u in captured_urls)


def test_scrape_caps_at_max_posts(monkeypatch: Any) -> None:
    def fake_api_get(*args: Any, **kwargs: Any) -> MagicMock:
        return _algolia_response(
            [
                {
                    "objectID": str(i),
                    "title": f"Co{i} (YC W22) Is Hiring SRE",
                    "url": f"https://example.com/{i}",
                }
                for i in range(20)
            ]
        )

    monkeypatch.setattr(hn_jobs_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_jobs_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_jobs_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_jobs_module.scrape_hn_jobs(_config(max_posts=5))
    assert len(jobs) == 5
