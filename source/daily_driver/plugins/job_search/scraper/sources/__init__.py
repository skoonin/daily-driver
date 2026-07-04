"""Per-source scraper implementations and the SCRAPERS registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from daily_driver.core.logging import get_logger

from .apple import scrape_apple
from .ashby import scrape_ashby
from .greenhouse import scrape_greenhouse
from .hn_jobs import scrape_hn_jobs
from .hn_who_is_hiring import scrape_hn_who_is_hiring
from .jobspy import scrape_jobspy
from .lever import scrape_lever
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
    "lever": scrape_lever,
    "workable": scrape_workable,
    "workday": scrape_workday,
    "linkedin": partial(scrape_jobspy, sites=["linkedin"]),
    "indeed": partial(scrape_jobspy, sites=["indeed"]),
    "apple": scrape_apple,
}


@dataclass(frozen=True)
class SourceCapability:
    """How a source enumerates and how a known row's liveness is verifiable.

    ``enumeration``: "full" -- one scrape returns the source's complete
    current inventory (an ATS board listing), so absence carries signal;
    "windowed" -- only a recent/top slice (search window, RSS feed), so
    absence means nothing.

    ``verify``: the liveness channel for a stored row. "board-diff" -- absent
    from a successful full enumeration = closed (free, rides the scrape);
    "url-check" -- per-row probe with a source-specific closed detector;
    "none" -- unverifiable (hard bot-wall), age-based fallback only.
    """

    enumeration: Literal["full", "windowed"]
    verify: Literal["board-diff", "url-check", "none"]


# Keyed by source_id, NOT attached to the scrape function: linkedin and indeed
# share one jobspy-backed callable but differ in verify capability. Consumed by
# the lifecycle phases (board-diff closure, jobs verify); declaration only --
# nothing here changes scrape behavior. Saturation detection is deliberately
# NOT keyed on this map: workday enumerates fully yet can still hit its page
# ceiling, so each source flags its own truncation.
# Rationale per source: greenhouse/ashby/lever/workable/workday return the
# complete board listing per configured company. apple returns NEW-only results
# behind search filters with slug-unstable URLs, so board-diff is impossible. indeed
# is bot-walled (no anonymous page fetch survives), so it cannot be verified.
SOURCE_CAPABILITIES: dict[str, SourceCapability] = {
    "remoteok": SourceCapability(enumeration="windowed", verify="url-check"),
    "weworkremotely": SourceCapability(enumeration="windowed", verify="url-check"),
    "hn_who_is_hiring": SourceCapability(enumeration="windowed", verify="url-check"),
    "hn_jobs": SourceCapability(enumeration="windowed", verify="url-check"),
    "greenhouse": SourceCapability(enumeration="full", verify="board-diff"),
    "ashby": SourceCapability(enumeration="full", verify="board-diff"),
    "lever": SourceCapability(enumeration="full", verify="board-diff"),
    "workable": SourceCapability(enumeration="full", verify="board-diff"),
    "workday": SourceCapability(enumeration="full", verify="board-diff"),
    "linkedin": SourceCapability(enumeration="windowed", verify="url-check"),
    "indeed": SourceCapability(enumeration="windowed", verify="none"),
    "apple": SourceCapability(enumeration="windowed", verify="url-check"),
}


__all__ = ["SCRAPERS", "SOURCE_CAPABILITIES", "SourceCapability"]
