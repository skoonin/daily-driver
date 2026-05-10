"""Skip / routing behavior for enrich_job_details (HN + Indeed + _api_get path).

Covers the rate-limit / bot-wall fixes:
- news.ycombinator.com URLs are skipped without an HTTP fetch (HN aggressively 429s).
- Indeed URLs (any regional host) are skipped (JobSpy already populates description;
  bare requests get bot-walled with 403).
- All other URLs flow through `_api_get`, inheriting retry / Retry-After / backoff.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from daily_driver.scraper.enrichment import enrich_job_details


@pytest.fixture
def fake_config() -> dict[str, Any]:
    return {
        "job_search": {
            "scraper": {
                "enabled": True,
                "timeout": 5,
                "max_retries": 1,
                "detail_delay_seconds": 0,
            }
        }
    }


def _job(url: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company": "Acme",
        "role": "SRE",
        "url": url,
        "source": "test",
    }
    base.update(overrides)
    return base


def test_hn_item_url_is_skipped_without_fetch(
    fake_config: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """news.ycombinator.com/item URLs must not trigger any HTTP request."""
    jobs = [
        _job("https://news.ycombinator.com/item?id=48066300"),
        _job("https://news.ycombinator.com/item?id=48061232"),
        _job("https://news.ycombinator.com/item?id=48054321"),
        _job("https://news.ycombinator.com/item?id=48049988"),
    ]
    with (
        patch("daily_driver.scraper.enrichment.requests.get") as bare_get,
        patch("daily_driver.scraper.enrichment._api_get") as api_get,
    ):
        with caplog.at_level("INFO", logger="daily_driver.scraper.enrichment"):
            enrich_job_details(jobs, fake_config)
    assert bare_get.call_count == 0
    assert api_get.call_count == 0
    # Per-run notice, not per-URL — must fire fewer times than URLs processed.
    skip_msgs = [r for r in caplog.records if "HN detail enrichment" in r.getMessage()]
    assert 1 <= len(skip_msgs) < len(jobs)


def test_indeed_url_is_skipped_without_fetch(
    fake_config: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Indeed URLs (any regional host) must not trigger any HTTP request."""
    jobs = [
        _job("https://ca.indeed.com/viewjob?jk=a3eb8be265b3f4a5"),
        _job("https://www.indeed.com/viewjob?jk=deadbeef"),
        _job("https://uk.indeed.com/viewjob?jk=cafebabe"),
    ]
    with (
        patch("daily_driver.scraper.enrichment.requests.get") as bare_get,
        patch("daily_driver.scraper.enrichment._api_get") as api_get,
    ):
        with caplog.at_level("INFO", logger="daily_driver.scraper.enrichment"):
            enrich_job_details(jobs, fake_config)
    assert bare_get.call_count == 0
    assert api_get.call_count == 0
    skip_msgs = [
        r for r in caplog.records if "Indeed detail enrichment" in r.getMessage()
    ]
    assert 1 <= len(skip_msgs) < len(jobs)


def test_other_urls_route_through_api_get(fake_config: dict[str, Any]) -> None:
    """Non-skipped hosts must fetch via `_api_get`, not bare `requests.get`."""
    jobs = [_job("https://boards.greenhouse.io/acme/jobs/123")]

    fake_resp = MagicMock()
    fake_resp.text = "<html></html>"

    with (
        patch("daily_driver.scraper.enrichment.requests.get") as bare_get,
        patch(
            "daily_driver.scraper.enrichment._api_get", return_value=fake_resp
        ) as api_get,
        patch(
            "daily_driver.scraper.enrichment._parse_detail_page", return_value={}
        ) as parse,
    ):
        enrich_job_details(jobs, fake_config)

    assert bare_get.call_count == 0
    assert api_get.call_count == 1
    # First positional arg is a Session built once for the run.
    session_arg, url_arg = api_get.call_args.args[0], api_get.call_args.args[1]
    assert url_arg == "https://boards.greenhouse.io/acme/jobs/123"
    assert session_arg is not None
    assert parse.call_count == 1


def test_api_get_returning_none_is_handled(fake_config: dict[str, Any]) -> None:
    """When `_api_get` gives up (None), enrichment continues without mutating the job."""
    jobs = [_job("https://boards.greenhouse.io/acme/jobs/123")]

    with (
        patch("daily_driver.scraper.enrichment._api_get", return_value=None),
        patch("daily_driver.scraper.enrichment._parse_detail_page") as parse,
    ):
        enrich_job_details(jobs, fake_config)

    # Parser must not be invoked when the fetch failed terminally.
    assert parse.call_count == 0
    assert "comp" not in jobs[0] or not jobs[0].get("comp")


def test_session_is_reused_across_jobs(fake_config: dict[str, Any]) -> None:
    """A single requests.Session is built once and reused across all fetches."""
    jobs = [
        _job("https://boards.greenhouse.io/acme/jobs/1"),
        _job("https://lever.co/acme/jobs/2"),
        _job("https://example.com/jobs/3"),
    ]

    fake_resp = MagicMock()
    fake_resp.text = "<html></html>"

    with (
        patch("daily_driver.scraper.enrichment._http_session") as build_session,
        patch(
            "daily_driver.scraper.enrichment._api_get", return_value=fake_resp
        ) as api_get,
        patch("daily_driver.scraper.enrichment._parse_detail_page", return_value={}),
    ):
        build_session.return_value = MagicMock()
        enrich_job_details(jobs, fake_config)

    assert build_session.call_count == 1
    assert api_get.call_count == 3
    sessions_used = {call.args[0] for call in api_get.call_args_list}
    assert len(sessions_used) == 1
