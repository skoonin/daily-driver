"""Anti-drift: ``fit_notes_target_indices`` must select exactly the rows
``_build_fit_plan`` enriches, so any consumer sharing the helper picks the same
slice. (Migrated from the deleted ``test_linkedin_descriptions.py`` when the
LinkedIn description fetcher was removed; selection is host-agnostic.)"""

from __future__ import annotations

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment.llm import (
    _build_fit_plan,
    fit_notes_target_indices,
)
from daily_driver.plugins.job_search.scraper.models import EnrichedJob
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched


@pytest.fixture
def ctx_on() -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
                "enrichment": {"detail_delay_seconds": 0},
            }
        )
    )


def _mixed_jobs() -> list[EnrichedJob]:
    return [
        make_enriched(url="https://x.com/1"),  # eligible
        make_enriched(url="https://x.com/a", fit=8, notes="done"),  # ineligible
        make_enriched(url="https://x.com/2"),  # eligible
        make_enriched(url="https://x.com/excluded"),  # eligible but excluded
        make_enriched(url="https://x.com/3"),  # eligible
    ]


def test_matches_build_fit_plan_selection(ctx_on: ScrapeContext) -> None:
    jobs = _mixed_jobs()
    exclude = frozenset({"https://x.com/excluded"})
    plan = _build_fit_plan(
        jobs, ctx_on, budget=None, progress=None, exclude_urls=exclude
    )
    assert plan is not None
    helper = fit_notes_target_indices(jobs, None, ctx_on.plugin.enrichment, exclude)
    assert helper == plan.target_idx
    # Input order preserved; ineligible (idx 1) and excluded (idx 3) dropped.
    assert helper == [0, 2, 4]


def test_budget_zero_selects_nothing(ctx_on: ScrapeContext) -> None:
    jobs = _mixed_jobs()
    plan = _build_fit_plan(jobs, ctx_on, budget=0, progress=None)
    assert plan is not None
    helper = fit_notes_target_indices(jobs, 0, ctx_on.plugin.enrichment)
    assert helper == plan.target_idx == []


def test_cap_honored(ctx_on: ScrapeContext) -> None:
    jobs = _mixed_jobs()
    plan = _build_fit_plan(jobs, ctx_on, budget=2, progress=None)
    assert plan is not None
    helper = fit_notes_target_indices(jobs, 2, ctx_on.plugin.enrichment)
    assert helper == plan.target_idx == [0, 2]
