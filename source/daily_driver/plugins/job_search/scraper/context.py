"""ScrapeContext and the scraper-layer exceptions / config accessors.

The typed input threaded through every scraper (:class:`ScrapeContext`), the
error types the pipeline raises, and the small config accessors sources read.
Sources import these directly; :mod:`runner` and :mod:`scrape_all` build and
thread the context.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from daily_driver.core.config_models import AIConfig
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.config import JobSearchPlugin, SourceToggle
from daily_driver.plugins.job_search.scraper.models import SaturationRecord

log = get_logger(__name__)


def _noop_report(done: int, total: int | None = None) -> None:
    """Default per-source progress reporter -- discards progress when no live
    display is attached, so scrapers can call ``ctx.report(...)`` unconditionally."""


def _noop_checkpoint(jobs: list[dict[str, Any]]) -> None:
    """Default per-unit checkpoint sink -- discards the batch when no durable sink
    is attached (dry-run, direct adapter tests), so a slow source can call
    ``ctx.checkpoint(...)`` unconditionally after each finished unit."""


@dataclass(frozen=True)
class ScrapeContext:
    """Typed inputs threaded through the scraper layer.

    Replaces the raw ``{"job_search": {...}, "ai": {...}, "context": ...,
    "_known_urls": ...}`` dict that every accessor used to re-validate.
    """

    plugin: JobSearchPlugin
    ai: AIConfig = field(default_factory=AIConfig)
    context_text: str = ""  # was the transient config["context"]
    known_urls: frozenset[str] = field(
        default_factory=frozenset
    )  # was config["_known_urls"]
    # Per-source live-progress reporter ``(done, total) -> None``. Bound per
    # source by the orchestrator (``_run_one``); every scraper reports against
    # its own natural unit (term×country, boards, categories, or a single fetch).
    report: Callable[[int, int | None], None] = _noop_report
    # Cooperative stop flag for a graceful Ctrl-C / SIGTERM during scraping.
    # KeyboardInterrupt lands only on the main thread; phase-1 sources run in
    # worker threads where it never arrives, so the orchestrator sets this event
    # on the first interrupt and each source checks it between its natural units
    # and returns the jobs accumulated so far (keeping completed work). Sources
    # mid-unit finish that one unit (bounded by the unit's own duration).
    stop_event: threading.Event = field(default_factory=threading.Event)
    # Per-unit durable checkpoint for the long-running sources: the jobspy-
    # backed sites (linkedin / indeed, unit = term x country) and the multi-
    # board sources (greenhouse/ashby/lever/workable/workday, unit = one
    # board). Bound per source by the orchestrator (``_run_one``) to hand each
    # finished unit's rows straight to the sink, so a crash / kill deep into a
    # multi-hour scrape or a ~1,200-board walk keeps every completed unit
    # instead of losing the whole source. The fast single-call sources
    # (remoteok, hn, wwr, apple) skip this -- their loss window on a crash is
    # seconds, not worth the per-unit lock churn -- and rely on the
    # end-of-source append instead.
    checkpoint: Callable[[list[dict[str, Any]]], None] = _noop_checkpoint
    # Saturation records appended by sources as they scrape (a query that
    # returned its full cap was truncated). Mutable shared list on a frozen
    # dataclass -- the runner reads it after scraping for the run summary and
    # the manifest's ``saturated_queries``.
    saturation: list[SaturationRecord] = field(default_factory=list)

    # Raw board enumerations recorded by full-listing adapters this run:
    # exact Source-cell label -> the COMPLETE pre-role-filter URL set of a
    # SUCCESSFULLY fetched board. Consumed by board-diff closure; a failed or
    # partial board records nothing, so its rows can never read as "gone".
    enumerations: dict[str, set[str]] = field(default_factory=dict)

    # Boards found by `jobs discover-boards` (matched cache), keyed by
    # platform. Populated by run() from the workspace state dir; the board
    # adapters (greenhouse/ashby/lever) union these with their hand-pinned
    # config boards, minus the per-platform exclude_boards blocklist.
    discovered_boards: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def record_enumeration(self, source_label: str, urls: set[str]) -> None:
        """Record one board's complete raw listing (pre role-filter).

        An empty set is a valid enumeration: a healthy board with zero open
        postings really has closed everything it once listed.
        """
        self.enumerations[source_label] = {
            url.strip() for url in urls if url and url.strip()
        }

    def record_saturation(
        self,
        *,
        source: str,
        query: str,
        returned: int,
        requested: int,
        kind: Literal["cap", "plateau"],
    ) -> None:
        """Record one truncated query: append + one -v log line.

        The single seam for saturation across sources, so every flagged query
        shows up in the -v log, not only jobspy's.
        """
        self.saturation.append(
            SaturationRecord(
                source=source,
                query=query,
                returned=returned,
                requested=requested,
                kind=kind,
            )
        )
        log.info(
            "[%s] saturated query %s: %d/%d returned (%s) -- coverage incomplete",
            source,
            query,
            returned,
            requested,
            kind,
        )


class ScraperError(RuntimeError):
    """Raised on unrecoverable scraper errors.

    Library code raises this instead of calling sys.exit() so the CLI layer
    can decide how to report and exit.
    """


class CheckpointAborted(Exception):
    """Raised out of ``ctx.checkpoint`` when a per-unit append fails.

    Signals a checkpointing source (jobspy, the board sources) to stop AT the
    failing unit and return what it has already persisted, instead of
    scraping on against a dead
    disk and then reporting "failed" while later units still land rows. The
    orchestrator has already recorded the source as failed by the time this is
    raised; the source catches it and returns early, so "failed" means "stopped
    at the failure". A no-checkpoint source never sees this.
    """


class PartialSourceError(Exception):
    """Signals a source finished but its result is INCOMPLETE (degraded).

    Raised by a source whose scrape only partly succeeded -- a paginated board
    that lost connectivity mid-walk, or a single-GET source where one or more
    board/account requests failed -- so its partial (or empty) job list would
    otherwise be indistinguishable from a clean, complete "0 found" scrape.

    Carries the jobs gathered so far (still appended and deduped, exactly as a
    normal return) plus a human-readable ``reason``. The orchestrator records
    the source as DEGRADED -- a state DISTINCT from failed (a source that
    raised an ordinary exception and produced nothing) -- and surfaces it in the
    end-of-run summary and the run manifest. A degraded source's rows are kept;
    only the completeness of its scrape is in doubt.
    """

    def __init__(self, jobs: list[dict[str, Any]], reason: str) -> None:
        super().__init__(reason)
        self.jobs = jobs
        self.reason = reason


# ── Source-toggle helper ─────────────────────────────────────────────────────


_T = TypeVar("_T", bound=SourceToggle)


def source_toggle(plugin: JobSearchPlugin, key: str, toggle_type: type[_T]) -> _T:
    """Return the typed toggle for a source, or a default instance if absent/wrong type.

    Sources whose per-source knobs live on a SourceToggle subclass read them
    through this helper: an unconfigured (or bare-bool) source yields a fresh
    ``toggle_type()`` carrying that subclass's defaults.
    """
    toggle = plugin.sources.get(key)
    return toggle if isinstance(toggle, toggle_type) else toggle_type()


def home_city(plugin: JobSearchPlugin) -> str:
    """Return the configured home city from the locations block."""
    loc = plugin.locations
    return (loc.home_city if loc and loc.home_city else None) or "Vancouver, BC"


def countries_list(plugin: JobSearchPlugin) -> list[str]:
    """Return the configured ISO country codes (map keys), defaulting to US + CA."""
    loc = plugin.locations
    return list(loc.countries) if loc and loc.countries else ["US", "CA"]
