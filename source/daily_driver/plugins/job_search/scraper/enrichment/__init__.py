"""Claude / detail-page enrichers (company, fit+notes, job details).

Every enricher takes a ``list[EnrichedJob]`` and returns ``(list[EnrichedJob],
stats)`` — one representation across the whole pipeline.
"""

from daily_driver.plugins.job_search.scraper.enrichment.detail import enrich_job_details
from daily_driver.plugins.job_search.scraper.enrichment.llm import (
    _location_summary,
    enrich_company_descriptions,
    enrich_fit_and_notes,
)

__all__ = [
    "_location_summary",
    "enrich_company_descriptions",
    "enrich_fit_and_notes",
    "enrich_job_details",
]
