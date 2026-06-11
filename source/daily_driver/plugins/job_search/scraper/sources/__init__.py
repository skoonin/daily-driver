"""Per-source scraper implementations and the SCRAPERS registry."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from daily_driver.core.logging import get_logger

from .apple import scrape_apple
from .greenhouse import scrape_greenhouse
from .hn_jobs import scrape_hn_jobs
from .hn_who_is_hiring import scrape_hn_who_is_hiring
from .jobspy import scrape_jobspy
from .remoteok import scrape_remoteok
from .weworkremotely import scrape_weworkremotely

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


# linkedin/indeed are site-named user-surface sources backed by python-jobspy.
# Each registry entry scrapes only its own site by default; when both are
# enabled with equal query knobs the runner merges them into one backend call
# (see runner._jobspy_scrape_plan). The single-site partials below are the
# fallback path (one site selected, or knobs differing between the two).
SCRAPERS: dict[str, Callable[[ScrapeContext], list[dict[str, Any]]]] = {
    "remoteok": scrape_remoteok,
    "weworkremotely": scrape_weworkremotely,
    "hn_who_is_hiring": scrape_hn_who_is_hiring,
    "hn_jobs": scrape_hn_jobs,
    "greenhouse": scrape_greenhouse,
    "linkedin": partial(scrape_jobspy, sites=["linkedin"]),
    "indeed": partial(scrape_jobspy, sites=["indeed"]),
    "apple": scrape_apple,
}


__all__ = ["SCRAPERS"]
