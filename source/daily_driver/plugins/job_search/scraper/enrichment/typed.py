"""Typed (EnrichedJob) wrappers around the dict-based enrichers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from daily_driver.plugins.job_search.scraper.enrichment.detail import enrich_job_details
from daily_driver.plugins.job_search.scraper.enrichment.llm import (
    enrich_company_descriptions,
    enrich_fit_and_notes,
)

if TYPE_CHECKING:
    from daily_driver.core.progress import ProgressCallback
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext


def enrich_company_descriptions_typed(
    jobs: list[EnrichedJob],  # noqa: F821
    ctx: ScrapeContext,
    *,
    budget: int = 0,
    progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, int]]:  # noqa: F821
    """Typed wrapper around ``enrich_company_descriptions``.

    Round-trips through a working-dict list because the legacy body mutates
    in place; returns a fresh list of frozen EnrichedJob instances built via
    ``model_copy(update=...)``.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import (
        _dict_to_enriched_updates,
        _enriched_to_dict,
    )

    working = [_enriched_to_dict(j) for j in jobs]
    stats = enrich_company_descriptions(working, ctx, budget=budget, progress=progress)
    out = [
        j.model_copy(update=_dict_to_enriched_updates(d))
        for j, d in zip(jobs, working, strict=True)
    ]
    return out, stats


def enrich_fit_and_notes_typed(
    jobs: list[EnrichedJob],  # noqa: F821
    ctx: ScrapeContext,
    *,
    budget: int = 0,
    progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, int]]:  # noqa: F821
    """Typed wrapper around ``enrich_fit_and_notes``."""
    from daily_driver.plugins.job_search.scraper.csv_io import (
        _dict_to_enriched_updates,
        _enriched_to_dict,
    )

    working = [_enriched_to_dict(j) for j in jobs]
    stats = enrich_fit_and_notes(working, ctx, budget=budget, progress=progress)
    out = [
        j.model_copy(update=_dict_to_enriched_updates(d))
        for j, d in zip(jobs, working, strict=True)
    ]
    return out, stats


def enrich_job_details_typed(
    jobs: list[EnrichedJob],  # noqa: F821
    ctx: ScrapeContext,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, int]]:  # noqa: F821
    """Typed wrapper around ``enrich_job_details``.

    The legacy ``enrich_job_details`` mutates in place and returns a stats
    dict; this wrapper produces a fresh list with each job's description_text /
    posted_date / comp populated where the detail fetch supplied them, and
    forwards the stats so callers can summarize the phase.
    """
    import datetime as dt

    from daily_driver.plugins.job_search.scraper.csv_io import (
        _dict_to_enriched_updates,
        _enriched_to_dict,
    )

    working = [_enriched_to_dict(j) for j in jobs]
    stats = enrich_job_details(working, ctx, progress=progress)
    out: list[Any] = []
    for j, d in zip(jobs, working, strict=True):
        updates = _dict_to_enriched_updates(d)
        # Detail enricher may also write posted_date as ISO string.
        if d.get("posted_date") and isinstance(d["posted_date"], str):
            try:
                updates["posted_date"] = dt.date.fromisoformat(d["posted_date"])
            except ValueError:
                pass
        # Comp from JSON-LD comes back via "comp" string; already handled.
        out.append(j.model_copy(update=updates))
    return out, stats
