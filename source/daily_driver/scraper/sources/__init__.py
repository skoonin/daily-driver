"""Per-source scraper implementations and the SCRAPERS registry."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .apple import scrape_apple
from .greenhouse import scrape_greenhouse
from .hn_jobs import scrape_hn_jobs
from .hn_who_is_hiring import scrape_hn_who_is_hiring
from .jobspy import (
    scrape_jobspy_google,
    scrape_jobspy_indeed,
    scrape_jobspy_linkedin,
)
from .remoteok import scrape_remoteok
from .weworkremotely import scrape_weworkremotely

log = logging.getLogger(__name__)


SCRAPERS: dict[str, Callable[[dict], list[dict]]] = {
    "remoteok": scrape_remoteok,
    "weworkremotely": scrape_weworkremotely,
    "hn_who_is_hiring": scrape_hn_who_is_hiring,
    "hn_jobs": scrape_hn_jobs,
    "greenhouse": scrape_greenhouse,
    "jobspy_linkedin": scrape_jobspy_linkedin,
    "jobspy_indeed": scrape_jobspy_indeed,
    "jobspy_google": scrape_jobspy_google,
    "apple": scrape_apple,
}


__all__ = ["SCRAPERS"]
