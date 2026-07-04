"""Runner-level scrape-plan for the jobspy-backed site sources.

``linkedin`` and ``indeed`` are separate registry ids backed by python-jobspy.
The runner always fetches each enabled site under its own site-named row -- one
``scrape_jobs`` call per site -- so each keeps its own progress row, retry, and
failure isolation. This module exercises that plan through
``_jobspy_scrape_plan``.
"""

from __future__ import annotations

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.scrape_all import _jobspy_scrape_plan


def _plugin(**sources: object) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate({"sources": sources})


def test_both_enabled_split_into_two_rows() -> None:
    plugin = _plugin(linkedin=True, indeed=True)
    plan = _jobspy_scrape_plan(["linkedin", "indeed"], plugin)
    # One plan entry per site, each under its own site-named row.
    assert plan == [("linkedin", ["linkedin"]), ("indeed", ["indeed"])]


def test_both_enabled_split_regardless_of_knobs() -> None:
    plugin = _plugin(
        linkedin={"enabled": True, "results_wanted_per_query": 50},
        indeed={"enabled": True, "results_wanted_per_query": 25},
    )
    plan = _jobspy_scrape_plan(["linkedin", "indeed"], plugin)
    assert plan == [("linkedin", ["linkedin"]), ("indeed", ["indeed"])]


def test_only_linkedin_enabled_single_row() -> None:
    plugin = _plugin(linkedin=True)
    plan = _jobspy_scrape_plan(["linkedin"], plugin)
    assert plan == [("linkedin", ["linkedin"])]


def test_only_indeed_enabled_single_row() -> None:
    plugin = _plugin(indeed=True)
    plan = _jobspy_scrape_plan(["indeed"], plugin)
    assert plan == [("indeed", ["indeed"])]


def test_no_jobspy_sites_empty_plan() -> None:
    plugin = _plugin(remoteok=True)
    assert _jobspy_scrape_plan([], plugin) == []
