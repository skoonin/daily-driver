"""Per-source scraper implementations and the SCRAPERS registry."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from daily_driver.core.logging import get_logger

from .apple import scrape_apple
from .ashby import scrape_ashby
from .greenhouse import scrape_greenhouse
from .hn_jobs import scrape_hn_jobs
from .hn_who_is_hiring import scrape_hn_who_is_hiring
from .jobspy import scrape_jobspy
from .remoteok import scrape_remoteok
from .weworkremotely import scrape_weworkremotely
from .workable import scrape_workable
from .workday import scrape_workday

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


# linkedin/indeed are site-named user-surface sources backed by python-jobspy.
# Each registry entry scrapes only its own site: the runner always fetches each
# enabled site in its own backend call under its own row (see
# runner._jobspy_scrape_plan), so each keeps its own progress row, retry, and
# failure isolation.
SCRAPERS: dict[str, Callable[[ScrapeContext], list[dict[str, Any]]]] = {
    "remoteok": scrape_remoteok,
    "weworkremotely": scrape_weworkremotely,
    "hn_who_is_hiring": scrape_hn_who_is_hiring,
    "hn_jobs": scrape_hn_jobs,
    "greenhouse": scrape_greenhouse,
    "ashby": scrape_ashby,
    "workable": scrape_workable,
    "workday": scrape_workday,
    "linkedin": partial(scrape_jobspy, sites=["linkedin"]),
    "indeed": partial(scrape_jobspy, sites=["indeed"]),
    "apple": scrape_apple,
}


__all__ = ["SCRAPERS"]
