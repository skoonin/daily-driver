"""Dedup/merge behavior for the scraper orchestrator.

Covers ``_merge_and_dedup``: when two boards surface the same role, which row
survives and how source-level failures are reported. First-scraper-wins is the
contract downstream consumers rely on, so the collision-resolution order is
asserted explicitly here.
"""

from __future__ import annotations


def test_merge_dedup_same_url_first_source_wins() -> None:
    """Two sources return the same URL: the first source's row survives."""
    from daily_driver.plugins.job_search.scraper import runner

    first = {"url": "https://x/y", "company": "Acme", "role": "SRE", "source": "a"}
    second = {"url": "https://x/y", "company": "Acme", "role": "SRE", "source": "b"}
    results = [("source_a", [first]), ("source_b", [second])]

    jobs, failed = runner._merge_and_dedup(results, {})

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

    jobs, failed = runner._merge_and_dedup(results, {})

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

    jobs, failed = runner._merge_and_dedup(results, {})

    assert jobs == [ok]
    assert failed == ["bad_src"]


def test_merge_dedup_empty_input_returns_empty() -> None:
    """No results in means empty jobs and empty failed_sources out."""
    from daily_driver.plugins.job_search.scraper import runner

    jobs, failed = runner._merge_and_dedup([], {})

    assert jobs == []
    assert failed == []
