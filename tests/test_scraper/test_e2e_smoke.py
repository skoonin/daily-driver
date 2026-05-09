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

from daily_driver.scraper import _impl


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
    jobs = _impl.scrape_remoteok(config)
    assert isinstance(jobs, list)
    if not jobs:
        pytest.skip("remoteok returned no rows (transient or no matches)")
    sample = jobs[0]
    for key in ("role", "company", "url", "source", "date_found"):
        assert key in sample, f"missing {key} in {sample}"


@pytest.mark.smoke
@pytest.mark.needs_jobspy
def test_jobspy_linkedin_fetch_description_accepted() -> None:
    """JobSpy 1.1.82+ accepts ``linkedin_fetch_description=True`` (W6 invariant).

    The whole point of W6's LinkedIn-parser deletion is that JobSpy now sources
    LinkedIn description + comp natively. If the pin slips below 1.1.82,
    ``scrape_jobspy`` will raise ``TypeError`` — the explicit catch in
    ``_impl.scrape_jobspy`` converts that to a logged error and an empty list,
    which this test detects via the empty result + a manual probe of the
    library version.
    """
    import jobspy  # type: ignore[import-untyped]

    pkg_version = getattr(jobspy, "__version__", "0.0.0")
    parts = pkg_version.split(".")[:3]
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError:
        pytest.skip(f"unparseable jobspy version {pkg_version!r}")
    assert (major, minor, patch) >= (1, 1, 82), (
        f"python-jobspy {pkg_version} too old; W6 requires >=1.1.82 for "
        "linkedin_fetch_description support"
    )

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
    jobs = _impl.scrape_jobspy(config)
    assert isinstance(jobs, list)
