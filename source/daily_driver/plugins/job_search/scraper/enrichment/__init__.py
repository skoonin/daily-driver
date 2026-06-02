"""Claude / detail-page enrichers (company, fit+notes, job details)."""

from __future__ import annotations

from daily_driver.plugins.job_search.scraper.enrichment.detail import enrich_job_details
from daily_driver.plugins.job_search.scraper.enrichment.llm import (
    _location_summary,
    enrich_company_descriptions,
    enrich_fit_and_notes,
)
from daily_driver.plugins.job_search.scraper.enrichment.typed import (
    enrich_company_descriptions_typed,
    enrich_fit_and_notes_typed,
    enrich_job_details_typed,
)

__all__ = [
    "_location_summary",
    "enrich_company_descriptions",
    "enrich_company_descriptions_typed",
    "enrich_fit_and_notes",
    "enrich_fit_and_notes_typed",
    "enrich_job_details",
    "enrich_job_details_typed",
]
