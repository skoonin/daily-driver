"""Scraper test helpers shared across the migrated EnrichedJob enricher tests."""

from __future__ import annotations

from typing import Any

from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    NormalizedJob,
    RawScrapedJob,
)


def make_enriched(
    *,
    company: str = "Acme",
    role: str = "SRE",
    url: str = "https://example.com/j",
    source: str = "remoteok",
    location: str = "Remote",
    comp: str = "",
    **overrides: Any,
) -> EnrichedJob:
    """Build an EnrichedJob for tests that used to hand-roll a working dict.

    ``overrides`` are applied via ``model_copy`` so callers can seed enrichment
    fields (fit, notes, description_text, status, ...) directly.
    """
    raw = RawScrapedJob(
        company=company,
        role=role,
        url=url,
        source=source,
        location=location,
        comp_display=comp,
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    return base.model_copy(update=dict(overrides))
