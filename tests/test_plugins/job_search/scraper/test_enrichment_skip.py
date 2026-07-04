"""Skip / routing behavior for enrich_job_details (HN + Indeed + _api_get path).

Covers the rate-limit / bot-wall fixes:
- news.ycombinator.com URLs are skipped without an HTTP fetch (HN aggressively 429s).
- Indeed URLs (any regional host) are skipped (JobSpy already populates description;
  bare requests get bot-walled with 403).
- All other URLs flow through `_api_get` (the single HTTP seam), inheriting
  retry / Retry-After / backoff.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import enrich_job_details
from daily_driver.plugins.job_search.scraper.models import EnrichedJob
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched


@pytest.fixture
def fake_config() -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {
                    "enabled": True,
                    "timeout": 5,
                    "max_retries": 1,
                },
                "enrichment": {
                    "detail_delay_seconds": 0,
                },
            }
        )
    )


def _job(url: str, **overrides: Any) -> EnrichedJob:
    return make_enriched(company="Acme", url=url, source="test", **overrides)


def test_hn_item_url_is_skipped_without_fetch(
    fake_config: ScrapeContext, caplog: pytest.LogCaptureFixture
) -> None:
    """news.ycombinator.com/item URLs must not trigger any HTTP request."""
    jobs = [
        _job("https://news.ycombinator.com/item?id=48066300"),
        _job("https://news.ycombinator.com/item?id=48061232"),
        _job("https://news.ycombinator.com/item?id=48054321"),
        _job("https://news.ycombinator.com/item?id=48049988"),
    ]
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get"
    ) as api_get:
        with caplog.at_level(
            "DEBUG", logger="daily_driver.plugins.job_search.scraper.enrichment"
        ):
            enrich_job_details(jobs, fake_config)
    # All HTTP now funnels through `_api_get`; a skipped host must not even
    # reach that single seam.
    assert api_get.call_count == 0
    # Per-run notice, not per-URL — must fire fewer times than URLs processed.
    skip_msgs = [r for r in caplog.records if "HN detail enrichment" in r.getMessage()]
    assert 1 <= len(skip_msgs) < len(jobs)


def test_indeed_url_is_skipped_without_fetch(
    fake_config: ScrapeContext, caplog: pytest.LogCaptureFixture
) -> None:
    """Indeed URLs (any regional host) must not trigger any HTTP request."""
    jobs = [
        _job("https://ca.indeed.com/viewjob?jk=a3eb8be265b3f4a5"),
        _job("https://www.indeed.com/viewjob?jk=deadbeef"),
        _job("https://uk.indeed.com/viewjob?jk=cafebabe"),
    ]
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get"
    ) as api_get:
        with caplog.at_level(
            "DEBUG", logger="daily_driver.plugins.job_search.scraper.enrichment"
        ):
            enrich_job_details(jobs, fake_config)
    assert api_get.call_count == 0
    skip_msgs = [
        r for r in caplog.records if "Indeed detail enrichment" in r.getMessage()
    ]
    assert 1 <= len(skip_msgs) < len(jobs)


def test_other_urls_route_through_api_get(fake_config: ScrapeContext) -> None:
    """Non-skipped hosts must fetch via `_api_get`, not bare `requests.get`."""
    jobs = [_job("https://apply.workable.com/acme/j/123")]

    fake_resp = MagicMock()
    fake_resp.text = "<html></html>"

    with (
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get",
            return_value=fake_resp,
        ) as api_get,
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._parse_detail_page",
            return_value={},
        ) as parse,
    ):
        enrich_job_details(jobs, fake_config)

    assert api_get.call_count == 1
    # First positional arg is a Session built once for the run.
    session_arg, url_arg = api_get.call_args.args[0], api_get.call_args.args[1]
    assert url_arg == "https://apply.workable.com/acme/j/123"
    assert session_arg is not None
    assert parse.call_count == 1


def test_progress_callback_fires_once_per_real_fetch(
    fake_config: ScrapeContext,
) -> None:
    """progress() advances per fetched job; skipped jobs only bump the skip tally."""
    jobs = [
        _job("https://apply.workable.com/acme/j/1"),
        _job("https://apply.workable.com/acme/j/2"),
        _job("https://news.ycombinator.com/item?id=1"),  # skipped (no fetch)
        _job("https://acme.com/job", comp="$200k"),  # skipped (has comp)
    ]
    fake_resp = MagicMock()
    fake_resp.text = "<html></html>"
    calls: list[tuple[int, str | None]] = []

    with (
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get",
            return_value=fake_resp,
        ),
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._parse_detail_page",
            return_value={},
        ),
    ):
        _out, stats = enrich_job_details(
            jobs, fake_config, progress=lambda n, d: calls.append((n, d))
        )

    assert len(calls) == 2  # only the two workable jobs do real work
    assert all(n == 1 for n, _ in calls)
    assert stats["fetched"] == 2
    assert stats["skipped"] == 2
    assert stats["total"] == 4


def test_api_get_returning_none_is_handled(fake_config: ScrapeContext) -> None:
    """When `_api_get` gives up (None), enrichment continues without mutating the job."""
    jobs = [_job("https://apply.workable.com/acme/j/123")]

    with (
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get",
            return_value=None,
        ),
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._parse_detail_page"
        ) as parse,
    ):
        out, _ = enrich_job_details(jobs, fake_config)

    # Parser must not be invoked when the fetch failed terminally.
    assert parse.call_count == 0
    assert not out[0].comp


def test_session_is_reused_across_jobs(fake_config: ScrapeContext) -> None:
    """A single requests.Session is built once and reused across all fetches."""
    jobs = [
        _job("https://apply.workable.com/acme/j/1"),
        _job("https://lever.co/acme/jobs/2"),
        _job("https://example.com/jobs/3"),
    ]

    fake_resp = MagicMock()
    fake_resp.text = "<html></html>"

    with (
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._http_session"
        ) as build_session,
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get",
            return_value=fake_resp,
        ) as api_get,
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.detail._parse_detail_page",
            return_value={},
        ),
    ):
        build_session.return_value = MagicMock()
        enrich_job_details(jobs, fake_config)

    assert build_session.call_count == 1
    assert api_get.call_count == 3
    sessions_used = {call.args[0] for call in api_get.call_args_list}
    assert len(sessions_used) == 1


def test_greenhouse_hosted_page_is_skipped_without_fetch(
    fake_config: ScrapeContext,
) -> None:
    """job-boards.greenhouse.io sits behind volume-based bot protection (403s
    at run scale, verified live 2026-07-04); its rows must never reach the
    HTTP seam. Applies to the legacy boards.greenhouse.io host too."""
    jobs = [
        _job("https://job-boards.greenhouse.io/trueanomalyinc/jobs/5108466007"),
        _job("https://boards.greenhouse.io/anthropic/jobs/123"),
    ]
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get"
    ) as api_get:
        _, stats = enrich_job_details(jobs, fake_config)
    assert api_get.call_count == 0
    assert stats["skip_reasons"].get("greenhouse: comp from scrape") == 2


def test_comp_filled_from_description_without_fetch(
    fake_config: ScrapeContext,
) -> None:
    """A comp-less row whose scraped description carries pay-transparency text
    gets its Comp cell from that text -- zero HTTP requests."""
    jobs = [
        _job(
            "https://job-boards.greenhouse.io/acme/jobs/1",
            description_text=(
                "About the role... California Base Salary: Senior: "
                "$150,000\u2013$205,000, Staff: $170,000 to $245,000."
            ),
        )
    ]
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get"
    ) as api_get:
        out, stats = enrich_job_details(jobs, fake_config)
    assert api_get.call_count == 0
    assert out[0].comp == "$150,000\u2013$205,000/yr"
    assert stats["enriched"] == 1
    assert stats["from_description"] == 1
    # A text-filled row is enriched, not skipped: it gained data.
    assert stats["skipped"] == 0


def test_description_comp_prevents_fetch_on_unblocked_hosts_too(
    fake_config: ScrapeContext,
) -> None:
    """The pre-pass runs before fetch targets are built, so any host's row
    with pay text in its description costs no request."""
    jobs = [
        _job(
            "https://example-ats.example.com/jobs/1",
            description_text="Salary: $120,000 - $150,000 plus benefits.",
        )
    ]
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get"
    ) as api_get:
        out, _ = enrich_job_details(jobs, fake_config)
    assert api_get.call_count == 0
    assert out[0].comp == "$120,000\u2013$150,000/yr"


def test_row_with_existing_comp_untouched_by_pre_pass(
    fake_config: ScrapeContext,
) -> None:
    """A row that already has comp keeps it verbatim (no re-derivation)."""
    jobs = [
        _job(
            "https://job-boards.greenhouse.io/acme/jobs/1",
            comp="$999,999/yr",
            description_text="Salary: $120,000 - $150,000.",
        )
    ]
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get"
    ) as api_get:
        out, _ = enrich_job_details(jobs, fake_config)
    assert api_get.call_count == 0
    assert out[0].comp == "$999,999/yr"


def test_triaged_row_not_touched_by_pre_pass(fake_config: ScrapeContext) -> None:
    """The detail pass never wrote to triaged rows; the description pre-pass
    keeps that footprint (status gate mirrors the fetch gate)."""
    jobs = [
        _job(
            "https://job-boards.greenhouse.io/acme/jobs/1",
            status="applied",
            description_text="Salary: $120,000 - $150,000.",
        )
    ]
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get"
    ) as api_get:
        out, _ = enrich_job_details(jobs, fake_config)
    assert api_get.call_count == 0
    assert out[0].comp == ""


def test_description_without_pay_text_still_fetches_elsewhere(
    fake_config: ScrapeContext,
) -> None:
    """No pay text in the description -> the row remains a fetch target on a
    host that allows it (workable/workday-class hosts keep their fetch)."""
    resp = MagicMock()
    resp.text = "<html></html>"
    jobs = [
        _job(
            "https://apply.workable.com/acme/j/1",
            description_text="A great role on a great team.",
        )
    ]
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.detail._api_get",
        return_value=resp,
    ) as api_get:
        enrich_job_details(jobs, fake_config)
    assert api_get.call_count == 1
