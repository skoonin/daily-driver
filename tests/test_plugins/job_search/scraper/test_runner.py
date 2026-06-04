"""Dedup/merge behavior for the scraper orchestrator.

Covers ``_merge_and_dedup``: when two boards surface the same role, which row
survives and how source-level failures are reported. First-scraper-wins is the
contract downstream consumers rely on, so the collision-resolution order is
asserted explicitly here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.config import JobSearchPlugin


def test_merge_dedup_same_url_first_source_wins() -> None:
    """Two sources return the same URL: the first source's row survives."""
    from daily_driver.plugins.job_search.scraper import runner

    first = {"url": "https://x/y", "company": "Acme", "role": "SRE", "source": "a"}
    second = {"url": "https://x/y", "company": "Acme", "role": "SRE", "source": "b"}
    results = [("source_a", [first]), ("source_b", [second])]

    jobs, failed = runner._merge_and_dedup(results)

    assert jobs == [first]
    assert failed == []


def test_merge_dedup_same_company_role_first_source_wins() -> None:
    """Same (company, role) key but different URLs: the first row survives.

    Distinct URLs would not collide on ``seen_urls``; the company+role key is
    what suppresses the cross-board duplicate.
    """
    from daily_driver.plugins.job_search.scraper import runner

    first = {"url": "https://a/1", "company": "Acme", "role": "SRE"}
    second = {"url": "https://b/2", "company": "Acme", "role": "SRE"}
    results = [("source_a", [first]), ("source_b", [second])]

    jobs, failed = runner._merge_and_dedup(results)

    assert jobs == [first]
    assert failed == []


def test_merge_dedup_exception_recorded_in_failed_sources() -> None:
    """A source whose result is an Exception lands in failed_sources, not jobs."""
    from daily_driver.plugins.job_search.scraper import runner

    ok = {"url": "https://a/1", "company": "Acme", "role": "SRE"}
    results = [
        ("good_src", [ok]),
        ("bad_src", RuntimeError("boom")),
    ]

    jobs, failed = runner._merge_and_dedup(results)

    assert jobs == [ok]
    assert failed == ["bad_src"]


def test_merge_dedup_empty_input_returns_empty() -> None:
    """No results in means empty jobs and empty failed_sources out."""
    from daily_driver.plugins.job_search.scraper import runner

    jobs, failed = runner._merge_and_dedup([])

    assert jobs == []
    assert failed == []


def _us_plugin() -> JobSearchPlugin:
    from daily_driver.plugins.job_search.config import JobSearchPlugin

    return JobSearchPlugin.model_validate(
        {
            "roles": ["Engineer"],
            "locations": {"countries": ["US"]},
        }
    )


def test_location_matches_accepts_city_plus_country() -> None:
    """Enriched 'City, Country' Apple strings must pass the country branch."""
    from daily_driver.plugins.job_search.scraper.runner import location_matches

    job = {"location": "Seattle, United States of America"}
    assert location_matches(job, _us_plugin()) is True


def test_location_matches_rejects_wrong_country() -> None:
    """The fix must not loosen the filter — wrong-country jobs still drop."""
    from daily_driver.plugins.job_search.scraper.runner import location_matches

    job = {"location": "Toronto, Canada"}
    assert location_matches(job, _us_plugin()) is False


def test_per_source_funnel_reconciles_and_drops_urlless() -> None:
    """found counts everything; new/known/loc_skip mirror the global pipeline.

    A url-less new job is counted as found but dropped (not 'new'), matching
    run()'s url-less filter, so per-source 'new' never exceeds the headline.
    """
    from daily_driver.plugins.job_search.scraper.runner import _per_source_funnel

    us = "Seattle, United States"
    results = [
        (
            "src",
            [
                {"url": "https://a/1", "company": "A", "role": "Eng", "location": us},
                {"url": "https://a/1", "company": "A", "role": "Eng", "location": us},
                {
                    "url": "https://known/1",
                    "company": "K",
                    "role": "Eng",
                    "location": us,
                },
                {
                    "url": "https://a/2",
                    "company": "B",
                    "role": "Eng",
                    "location": "Berlin, Germany",
                },
                {"url": "", "company": "NoUrl", "role": "Eng", "location": us},
            ],
        )
    ]
    funnel = _per_source_funnel(results, {"https://known/1"}, set(), _us_plugin())
    c = funnel["src"]
    assert c["found"] == 5
    assert c["new"] == 1  # only the url-bearing US job; url-less one dropped
    assert c["known"] == 1
    assert c["loc_skip"] == 1  # Berlin, wrong country
    assert "dup" not in c  # the dead counter is gone
