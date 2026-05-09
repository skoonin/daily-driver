"""End-to-end smoke tests against live URLs.

Off by default — `pyproject.toml` adds `-m 'not smoke'` to pytest's `addopts`.
Run explicitly with::

    pytest -m smoke tests/test_scraper/test_e2e_smoke.py

Goal: catch regressions where a code change passes unit tests but breaks
against a real source (e.g., JobSpy version drift, RemoteOK feed schema
changes). These tests deliberately tolerate transient failures (rate limits,
empty result sets) — they assert *shape* and *call signature*, not row counts.
"""

from __future__ import annotations

import pytest

from daily_driver.scraper.sources.jobspy import scrape_jobspy
from daily_driver.scraper.sources.remoteok import scrape_remoteok


@pytest.mark.smoke
def test_remoteok_live_returns_list_with_expected_fields() -> None:
    """RemoteOK RSS scrape returns rows with the canonical job-dict shape.

    Cheap and reliable — RemoteOK serves a static RSS feed without auth or JS.
    Mainly catches breakage when their feed schema drifts.
    """
    config = {
        "job_search": {
            "roles": ["software engineer"],
            "scraper": {
                "enabled": True,
                "search_terms": ["software"],
            },
        }
    }
    jobs = scrape_remoteok(config)
    assert isinstance(jobs, list)
    if not jobs:
        pytest.skip("remoteok returned no rows (transient or no matches)")
    sample = jobs[0]
    for key in ("role", "company", "url", "source", "date_found"):
        assert key in sample, f"missing {key} in {sample}"


@pytest.mark.smoke
@pytest.mark.needs_jobspy
def test_jobspy_linkedin_fetch_description_accepted() -> None:
    """JobSpy accepts ``linkedin_fetch_description=True`` against a live call.

    Pip pin (`python-jobspy>=1.1.82`) + the ``TypeError`` hard-fail in
    ``scrape_jobspy`` enforce the version contract; this test exercises the
    real call path end-to-end. Asserts shape only — empty results are fine
    (LinkedIn rate-limits, search may not match anything in `hours_old`).
    """
    config = {
        "job_search": {
            "roles": ["software engineer"],
            "locations": {"countries": ["US"]},
            "scraper": {
                "enabled": True,
                "search_terms": ["software engineer"],
                "jobspy": {"results_wanted_per_query": 3, "hours_old": 168},
            },
        }
    }
    jobs = scrape_jobspy(config)
    assert isinstance(jobs, list)
