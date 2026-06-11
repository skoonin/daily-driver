"""Runner-level merge-vs-split decision for the jobspy-backed site sources.

``linkedin`` and ``indeed`` are separate registry ids backed by python-jobspy.
The runner merges them into ONE ``scrape_jobs`` call when both are enabled and
their shared query knobs (``results_wanted_per_query`` / ``hours_old``) are
equal; when the knobs differ it falls back to per-site calls. This module
exercises that decision through ``_jobspy_scrape_plan``.
"""

from __future__ import annotations

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import _jobspy_scrape_plan


def _plugin(**sources: object) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate({"sources": sources})


def test_both_enabled_equal_knobs_merge_into_one_row() -> None:
    plugin = _plugin(linkedin=True, indeed=True)
    plan = _jobspy_scrape_plan(["linkedin", "indeed"], plugin)
    # One merged plan entry: row id is the joined site label, both sites in one call.
    assert plan == [("linkedin + indeed", ["linkedin", "indeed"])]


def test_both_enabled_differing_knobs_split_into_two_rows() -> None:
    plugin = _plugin(
        linkedin={"enabled": True, "results_wanted_per_query": 50},
        indeed={"enabled": True, "results_wanted_per_query": 25},
    )
    plan = _jobspy_scrape_plan(["linkedin", "indeed"], plugin)
    assert plan == [("linkedin", ["linkedin"]), ("indeed", ["indeed"])]


def test_differing_hours_old_also_splits() -> None:
    plugin = _plugin(
        linkedin={"enabled": True, "hours_old": 168},
        indeed={"enabled": True, "hours_old": 72},
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
