"""Scraper orchestration: ScrapeContext, dedup logic, run() / run_backfill()."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager, ExitStack, nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from daily_driver.core.clock import today
from daily_driver.core.config_models import AIConfig
from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import (
    adopt_third_party_loggers,
    get_logger,
    live_log_window,
)
from daily_driver.core.progress import Group, Item, Phase, RunProgress
from daily_driver.plugins.job_search.config import (
    JobSearchPlugin,
    SourceToggle,
)
from daily_driver.plugins.job_search.jobs_lock import (
    LOCK_GIVEUP_MESSAGE,
    LOCK_WAIT_TIMEOUT_SECONDS,
    clear_stale_adjacent_lock,
    jobs_lock_path,
    workspace_busy_notice,
)
from daily_driver.plugins.job_search.scraper.countries import country_names
from daily_driver.plugins.job_search.scraper.enrichment.llm import _empty_fit_stats
from daily_driver.plugins.job_search.scraper.models import SaturationRecord
from daily_driver.plugins.job_search.scraper.sources import SCRAPERS
from daily_driver.plugins.job_search.scraper.sources._http import (
    HTTPError,
    HTTPTimeout,
)
from daily_driver.plugins.job_search.scraper.sources.jobspy import scrape_jobspy

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob

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
    # Per-unit durable checkpoint for the multi-HOUR jobspy-backed sources
    # (linkedin / indeed). Bound per source by the orchestrator (``_run_one``) to
    # hand each finished (term x country) unit's rows straight to the sink, so a
    # crash / kill two hours in keeps every completed unit instead of losing the
    # whole source. The fast single-call sources (remoteok, hn, greenhouse, wwr,
    # apple) skip this -- their loss window on a crash is seconds, not worth the
    # per-unit lock churn -- and rely on the end-of-source append instead.
    checkpoint: Callable[[list[dict[str, Any]]], None] = _noop_checkpoint
    # Saturation records appended by sources as they scrape (a query that
    # returned its full cap was truncated). Mutable shared list on a frozen
    # dataclass -- the runner reads it after scraping for the run summary and
    # the manifest's ``saturated_queries``.
    saturation: list[SaturationRecord] = field(default_factory=list)

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

    Signals a checkpointing source (jobspy) to stop AT the failing unit and
    return what it has already persisted, instead of scraping on against a dead
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
    """Return the configured ISO country codes, defaulting to US + CA."""
    loc = plugin.locations
    return list(loc.countries) if loc and loc.countries else ["US", "CA"]


def location_matches(job: dict[str, Any], plugin: JobSearchPlugin) -> bool:
    """Check whether a job's location matches the configured allow-list.

    Accepts if any of:
      - remote: true and job location contains "remote" (or is empty/missing)
      - job location contains a country name from locations.countries
    Returns True (accept) when no locations block is configured.
    """
    loc_cfg = plugin.locations
    if loc_cfg is None:
        return True

    loc = job.get("location", "").lower()
    if not loc:
        # Empty location implies remote-friendly; accept when remote is enabled
        return loc_cfg.remote

    if loc_cfg.remote and "remote" in loc:
        return True

    for code in loc_cfg.countries:
        for name in country_names(code):
            if name.lower() in loc:
                return True

    return False


# ── Dedup helpers ─────────────────────────────────────────────────────────────


_WHITESPACE_RE = re.compile(r"\s+")


def _dedup_norm(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", s.lower().strip())


def dedup_key(company: str, role: str) -> str:
    """Normalized dedup key for cross-site duplicate detection.

    Lowercases and collapses whitespace in both fields so the same job posted
    on RemoteOK and LinkedIn produces an identical key.
    """
    return f"{_dedup_norm(company)}::{_dedup_norm(role)}"


# Remediation hint per (source, kind) for the run-summary Saturated line.
# Per-source because each has its own limit knob; workday's page ceiling is a
# code constant, not config.
_SATURATION_HINTS: dict[tuple[str, str], str] = {
    ("linkedin", "cap"): (
        "results capped; raise results_wanted_per_query or split terms"
    ),
    ("indeed", "cap"): (
        "results capped; raise results_wanted_per_query or split terms"
    ),
    ("linkedin", "plateau"): (
        "stopped early at/past the ~100/IP rate-limit zone; "
        "deeper coverage needs proxies"
    ),
    ("workday", "cap"): "page ceiling hit; some postings unfetched",
    ("apple", "cap"): "scroll cap hit; raise scraper.max_pages",
}


# Cells the enrichment waves may fill on a pre-existing (backlog) row. The
# in-place write-back is restricted to these so a concurrent hand-edit to any
# other cell (Status, Location, dates, ...) survives the run.
_ENRICHMENT_OWNED_CELLS: tuple[str, ...] = (
    "Fit",
    "Comp",
    "Notes",
    "Remote",
    "Date Enriched",
)


def _csv_row_identity(row: dict[str, str]) -> str:
    """Stable identity for a jobs.csv row: stripped Link URL, else dedup key.

    Mirrors the append-time dedup boundary (URL primary, ``(company, role)``
    fallback) so a row's identity is the same whether it is held in memory as an
    ``EnrichedJob`` or read back as a raw CSV dict. Used by ``_JobSink.flush`` to
    merge this run's rows against the current on-disk rows by identity.
    """
    url = (row.get("Link") or "").strip()
    if url:
        return url
    # csv.DictReader fills missing trailing cells with None for a row shorter
    # than the header (a hand-edit slip), so coerce to str -- dedup_key lower()s
    # its inputs and would otherwise raise AttributeError on None mid-flush.
    return dedup_key(row.get("Company") or "", row.get("Role") or "")


# Location strings scrapers emit for fully-remote roles. All collapse to "Remote"
# so downstream consumers see one canonical value.
_REMOTE_LOCATION_ALIASES: frozenset[str] = frozenset(
    {
        "",
        "anywhere",
        "worldwide",
        "remote - anywhere",
        "remote, worldwide",
    }
)

# Role-title suffixes appended by some scrapers to signal remote eligibility.
# Stripped here so dedup and display see clean titles.
_REMOTE_ROLE_SUFFIXES: tuple[str, ...] = (" (remote)", "(remote)", " - remote")


def _enriched_from_scraped(job: dict[str, Any]) -> EnrichedJob:  # noqa: F821
    """Lift one merged source dict into a fully-typed ``EnrichedJob``.

    The source-adapter boundary stays dict-based: scrapers emit raw dicts
    (``comp`` display string, ISO ``date_found``, optional ``description_text``).
    This validates that dict through ``RawScrapedJob`` — the one place the
    pipeline crosses from untyped wire data into the typed models — then runs
    the standard ``from_raw`` -> ``from_normalized`` transitions. ``description_text``
    is threaded separately because ``RawScrapedJob`` does not model enrichment
    fields.
    """
    import datetime as dt

    from daily_driver.plugins.job_search.scraper.models import (
        EnrichedJob,
        NormalizedJob,
        RawScrapedJob,
    )
    from daily_driver.plugins.job_search.scraper.normalize_location import (
        detect_remote,
        normalize_location,
    )

    date_found = job.get("date_found")
    if isinstance(date_found, str) and date_found:
        try:
            date_found_val: dt.date = dt.date.fromisoformat(date_found)
        except ValueError:
            date_found_val = dt.date.today()  # noqa: DTZ011
    elif isinstance(date_found, dt.date):
        date_found_val = date_found
    else:
        date_found_val = dt.date.today()  # noqa: DTZ011

    raw = RawScrapedJob.model_validate(
        {
            "company": job.get("company", ""),
            # Coerce on STRIPPED content: a whitespace-only role is truthy, so a
            # bare `or` fallback would keep it, and RawScrapedJob then strips it to
            # '' and the NonEmptyStr validator raises -- aborting the run.
            "role": (job.get("role") or "").strip() or "(unknown)",
            "url": job.get("url", ""),
            "source": job.get("source", "") or "unknown",
            "location": job.get("location", ""),
            "comp_display": job.get("comp", "") or "",
            "date_found": date_found_val,
        }
    )
    enriched = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))

    # Location becomes geography-only, country-first; remote-ness moves to the
    # Remote column. Both read the RAW scraped location/role (the location filter
    # already ran on that raw text upstream), with the per-search origin-country
    # ISO code as the country fallback when the text names none.
    raw_location = job.get("location", "") or ""
    origin_country = job.get("origin_country") or None
    location = normalize_location(raw_location, origin_country)
    remote = detect_remote(raw_location, job.get("role", "") or "")

    updates: dict[str, Any] = {"location": location, "remote": remote}
    desc = job.get("description_text", "")
    if desc:
        updates["description_text"] = desc
    return enriched.model_copy(update=updates)


# Sources that require a full (non-headless) browser. SourceToggle has
# extra="forbid" so a config-based `type: playwright` key is rejected by
# pydantic -- browser classification must live in code, not config.
_PLAYWRIGHT_SOURCES: frozenset[str] = frozenset({"apple"})

# Site-named sources backed by python-jobspy. Each enabled site is fetched in
# its own backend call under its own row (see _jobspy_scrape_plan); the library
# is an implementation detail, never part of the user surface.
_JOBSPY_SITES: tuple[str, ...] = ("linkedin", "indeed")

# Display names for the live scraping rows. Source ids are pipeline-internal;
# these are what a user reads. Unmapped ids fall back to a de-underscored form.
_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "weworkremotely": "we work remotely",
    "hn_who_is_hiring": "hn who's hiring",
    "hn_jobs": "hn jobs",
}


def _jobspy_scrape_plan(
    jobspy_sites: list[str], plugin: JobSearchPlugin
) -> list[tuple[str, list[str]]]:
    """Decide how the enabled jobspy-backed sites are fetched.

    Returns ``(row_id, sites)`` entries -- always one call per enabled site,
    each under its own site-named row. Each site keeps its own progress row,
    retry, and failure isolation: in practice Indeed finishes quickly while only
    LinkedIn is slow, so a combined row / combined failure unit is not worth the
    merge. ``jobspy_sites`` preserves registry order. ``plugin`` is unused now
    that there is no merge predicate; kept for a stable call signature.
    """
    return [(s, [s]) for s in _JOBSPY_SITES if s in jobspy_sites]


def _display_name(source_id: str) -> str:
    return _SOURCE_DISPLAY_NAMES.get(source_id, source_id.replace("_", " "))


# Per-source expectation notes shown on a live row while it runs. Some boards go
# quiet for minutes mid-scrape; a note set once at start time (race-free, no
# timer) reassures the user the row isn't stuck. Unmapped sources show a plain
# "running" marker.
_SLOW_SOURCE_NOTES: dict[str, str] = {
    # Apple runs headless while the live block is pinned (force_headless), so the
    # note describes the slow headless browser scrape, not a visible window.
    "apple": "running -- headless browser scrape, can take a minute",
}

_JOBSPY_SLOW_NOTE = "running -- can take several minutes"


def _slow_source_note(source_id: str) -> str | None:
    """The running-state note for a known-slow source, or None for the default.

    Each jobspy-backed site runs under its own row id (``linkedin`` / ``indeed``)
    and can take minutes; both get the slow-source note.
    """
    if source_id in _JOBSPY_SITES:
        return _JOBSPY_SLOW_NOTE
    return _SLOW_SOURCE_NOTES.get(source_id)


def _fmt_duration(seconds: float) -> str:
    """Human elapsed: ``6m 41s`` for a minute or more, else ``6.1s``."""
    if seconds >= 60:
        minutes, secs = divmod(int(round(seconds)), 60)
        return f"{minutes}m {secs}s"
    return f"{seconds:.1f}s"


def _intra_source_duplicates(jobs: list[dict[str, Any]]) -> int:
    """Count a source's own duplicate rows (same URL or company+role key).

    Apple's locale loop, for example, returns the same posting under several
    locales; this surfaces that on the source row without waiting for the
    cross-source merge.
    """
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    unique = 0
    for job in jobs:
        url = job.get("url", "")
        key = dedup_key(job.get("company", ""), job.get("role", ""))
        if (url and url in seen_urls) or (key and key in seen_keys):
            continue
        if url:
            seen_urls.add(url)
        if key:
            seen_keys.add(key)
        unique += 1
    return len(jobs) - unique


def _ctx_with_headless(ctx: ScrapeContext, headless: bool) -> ScrapeContext:
    """Return a new ScrapeContext with job_search.scraper.headless overridden.

    Scrapers read headless via ``ctx.plugin.scraper.headless``; handing each
    phase its own context lets the orchestrator force headless mode per phase
    without threading a kwarg through every scraper function.
    """
    return replace(
        ctx,
        plugin=ctx.plugin.model_copy(
            update={
                "scraper": ctx.plugin.scraper.model_copy(update={"headless": headless})
            }
        ),
    )


def _run_one(
    source_id: str,
    ctx: ScrapeContext,
    on_source_done: Callable[[str, bool, str], None] | None = None,
    on_source_start: Callable[[str], None] | None = None,
    on_source_progress: Callable[[str, int, int | None], None] | None = None,
    scraper_fn: Callable[[ScrapeContext], list[dict[str, Any]]] | None = None,
    on_source_checkpoint: Callable[[str, list[dict[str, Any]]], None] | None = None,
    on_source_degraded: Callable[[str, str], None] | None = None,
) -> list[dict[str, Any]] | Exception:
    """Invoke one scraper and map known failures to exceptions.

    Returns the job list on success, or the caught exception on failure. The
    orchestrator classifies exceptions into failed_sources during merge.

    Brackets the scrape with ``on_source_start(source_id)`` and
    ``on_source_done(source_id, ok, detail)`` so the live display can open a
    per-source row when the work begins and freeze it with the job count or
    failure reason when it ends. ``on_source_progress`` is bound onto the
    context as ``ctx.report(done, total)`` so the scraper drives the per-source
    bar against its own natural unit (term×country, boards, categories, or a
    single fetch). All three drive only the thread-safe progress display, so
    they are safe to invoke from the Phase-1 worker threads.

    ``scraper_fn`` overrides the registry lookup; the jobspy-backed group passes
    a per-site scrape bound to a row id (``linkedin`` / ``indeed``).

    ``on_source_checkpoint`` is bound onto the context as ``ctx.checkpoint(batch)``
    so a slow source (jobspy) hands each finished (term x country) unit straight
    to the durable sink as it completes. A source that checkpoints is appended
    incrementally, so the coordinator SKIPS its end-of-source append (it tracks
    which sources checkpointed); ``_run_one`` still returns the full job list for
    the run manifest's sources-ok tally and cross-source dedup.

    ``on_source_degraded(source_id, reason)`` fires when a source raises
    ``PartialSourceError`` -- it completed but its scrape is incomplete. Its
    partial jobs are returned normally (so they still append and dedup), but the
    orchestrator records the source as degraded (distinct from failed) for the
    summary and manifest.
    """
    if scraper_fn is None:
        scraper_fn = SCRAPERS[source_id]
    timeout = ctx.plugin.scraper.timeout
    log.info("[%s] starting", source_id)
    if on_source_start is not None:
        on_source_start(source_id)
    if on_source_progress is not None:
        report_cb = on_source_progress  # narrow for the closure below

        def _report(done: int, total: int | None = None) -> None:
            report_cb(source_id, done, total)

        ctx = replace(ctx, report=_report)
    if on_source_checkpoint is not None:
        checkpoint_cb = on_source_checkpoint  # narrow for the closure below

        def _checkpoint(batch: list[dict[str, Any]]) -> None:
            checkpoint_cb(source_id, batch)

        ctx = replace(ctx, checkpoint=_checkpoint)
    start = time.perf_counter()
    degraded_reason: str | None = None
    try:
        jobs = scraper_fn(ctx)
    except PartialSourceError as exc:
        # Degraded, not failed: the source completed but one or more of its units
        # failed mid-scrape, so its (possibly empty) result is incomplete. Keep
        # the jobs it did gather -- they still flow through the normal append/dedup
        # path below -- and record the source as degraded so the summary and
        # manifest do not read the incomplete scrape as a clean run.
        jobs = exc.jobs
        degraded_reason = exc.reason
        log.warning("[%s] degraded: %s", source_id, exc.reason)
        if on_source_degraded is not None:
            on_source_degraded(source_id, exc.reason)
    except HTTPTimeout as exc:
        # HTTPTimeout/HTTPError alias requests exceptions; the stub-less
        # `requests` import types them as Any, so narrow on the way out.
        log.warning("[%s] timed out after %ds", source_id, timeout)
        if on_source_done is not None:
            on_source_done(source_id, False, f"failed (timed out after {timeout}s)")
        return cast(Exception, exc)
    except HTTPError as exc:
        log.warning("[%s] request failed: %s", source_id, exc)
        if on_source_done is not None:
            on_source_done(source_id, False, f"failed ({exc})")
        return cast(Exception, exc)
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] unexpected error: %s", source_id, exc, exc_info=True)
        if on_source_done is not None:
            on_source_done(source_id, False, f"failed ({exc})")
        return exc
    elapsed = time.perf_counter() - start
    log.info("[%s] took %.1fs (%d jobs)", source_id, elapsed, len(jobs))
    # Intra-source duplicates (e.g. apple's locale overlap) are diagnostic, not
    # the dedup the user cares about (already-in-csv); surface only at -v.
    intra_dup = _intra_source_duplicates(jobs)
    if intra_dup:
        log.info(
            "[%s] scrape complete: %d entries were intra-source duplicates",
            source_id,
            intra_dup,
        )
    if on_source_done is not None:
        # An honest note when the source stopped early on a graceful interrupt:
        # it kept what it had fetched before the stop, not a complete scrape.
        if ctx.stop_event.is_set():
            detail = f"interrupted -- {len(jobs)} found so far"
        elif degraded_reason is not None:
            detail = f"degraded -- {len(jobs)} found ({degraded_reason})"
        else:
            detail = f"{len(jobs)} found in {_fmt_duration(elapsed)}"
        on_source_done(source_id, True, detail)
    return jobs


def _merge_and_dedup(
    results: list[tuple[str, list[dict[str, Any]] | Exception]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge per-source results, deduplicating by URL and company+role key.

    First-scraper-wins: iteration order of `results` determines which job wins
    a dedup collision. Exceptions are collected into failed_sources.
    """
    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    failed_sources: list[str] = []

    for source_id, result in results:
        if isinstance(result, Exception):
            failed_sources.append(source_id)
            continue
        for job in result:
            url = job.get("url", "")
            key = dedup_key(job.get("company", ""), job.get("role", ""))
            if (url and url in seen_urls) or (key and key in seen_keys):
                continue
            if url:
                seen_urls.add(url)
            if key:
                seen_keys.add(key)
            all_jobs.append(job)

    return all_jobs, failed_sources


def _per_source_funnel(
    results: list[tuple[str, list[dict[str, Any]] | Exception]],
    known_urls: set[str],
    known_keys: set[str],
    plugin: JobSearchPlugin,
) -> dict[str, dict[str, int]]:
    """Per-source breakdown: found / new / known (already in csv) / loc_skip.

    Mirrors the global pipeline exactly -- cross-source dedup (first-wins),
    then the already-known check, then the url-less drop, then the location
    filter -- so the per-source ``new``/``loc_skip`` counts reconcile with the
    Completed line. Within-run duplicates and url-less new jobs are dropped
    (not tallied), matching what the pipeline writes.
    """
    stats: dict[str, dict[str, int]] = {}
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    for source_id, result in results:
        if isinstance(result, Exception):
            continue
        counts = stats.setdefault(
            source_id, {"found": 0, "new": 0, "known": 0, "loc_skip": 0}
        )
        for job in result:
            counts["found"] += 1
            url = job.get("url", "")
            key = dedup_key(job.get("company", ""), job.get("role", ""))
            if (url and url in seen_urls) or (key and key in seen_keys):
                continue  # within-run duplicate
            if url:
                seen_urls.add(url)
            if key:
                seen_keys.add(key)
            if (url and url in known_urls) or (key and key in known_keys):
                counts["known"] += 1
            elif not url:
                continue  # url-less new jobs are dropped before the funnel
            elif location_matches(job, plugin):
                counts["new"] += 1
            else:
                counts["loc_skip"] += 1
    return stats


# Per-source outcome-bar segment colours. enlighten resolves these through
# blessed; "bright_black" renders as grey for the leftover (within-run duplicates
# and url-less rows that are found but never reach the new/known/loc_skip funnel).
_SEG_NEW_COLOR = "green"
_SEG_KNOWN_COLOR = "magenta"  # not blue: the in-progress source bar fills blue
_SEG_LOC_SKIP_COLOR = "yellow"
_SEG_OTHER_COLOR = "bright_black"


def _funnel_other(counts: dict[str, int]) -> int:
    """The unclassified remainder of a per-source funnel: ``found`` minus the
    classified survivors (``new`` / ``known`` / ``loc_skip``), clamped at 0.

    The funnel tallies only deduped survivors, so this is the within-run
    duplicate + url-less rows that were found but never classified.
    """
    return max(
        0,
        counts.get("found", 0)
        - counts.get("new", 0)
        - counts.get("known", 0)
        - counts.get("loc_skip", 0),
    )


def _source_breakdown_segments(counts: dict[str, int]) -> list[tuple[int, str]]:
    """Map a per-source funnel to coloured segments for its finished bar.

    ``counts`` carries ``found`` / ``new`` / ``known`` / ``loc_skip`` (see
    :func:`_per_source_funnel`). Returns ``(count, colour)`` pairs that
    :meth:`Item.show_breakdown` stacks into the bar; their counts sum to
    ``found`` so the fill is the whole result.
    """
    return [
        (counts.get("new", 0), _SEG_NEW_COLOR),
        (counts.get("known", 0), _SEG_KNOWN_COLOR),
        (counts.get("loc_skip", 0), _SEG_LOC_SKIP_COLOR),
        (_funnel_other(counts), _SEG_OTHER_COLOR),
    ]


class _JobSink:
    """The durable-record checkpoint for one run: appends each source's rows to
    jobs.csv as it lands, then lets enrichment update those rows in place with
    periodic flushes.

    The sink is the single owner of the run's growing ``rows`` list and the
    dedup state. ``append_source`` dedups one source's scraped dicts (against the
    known url/key sets PLUS rows appended earlier this run), drops url-less rows,
    location-filters, lifts survivors to ``EnrichedJob``, appends them to
    jobs.csv under the sentinel lock, and folds them into ``rows`` so a later
    source (the Apple wave) dedups against them. ``flush`` rewrites the whole
    file -- ``preexisting_rows`` (what was on disk before the run, carried
    through verbatim) followed by ``rows`` -- through the one backfill rewrite
    path so a mid-enrichment crash loses at most one flush window. All sink
    methods run on the
    coordinator thread; no internal locking beyond the per-call sentinel lock,
    which is held only around each I/O burst — never across LLM calls.
    """

    def __init__(
        self,
        *,
        csv_path: Path,
        lock_path: Path,
        header: list[str],
        known_urls: set[str],
        known_keys: set[str],
        plugin: JobSearchPlugin,
        external_lock_held: bool = False,
        preexisting_rows: list[dict[str, str]] | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        self.csv_path = csv_path
        self.lock_path = lock_path
        self.header = header
        # Sidecar description store (url -> text), loaded once by the caller.
        # Folded into by append_source, persisted by flush -- see the
        # descriptions module docstring for why this lives outside jobs.csv.
        self._descriptions = descriptions or {}
        self._descriptions_dirty = False
        # backfill holds the sentinel ``file_lock`` for its whole lifecycle (read
        # -> enrich -> rewrite) so a concurrent run's appends can't slip into the
        # window a backfill rewrite would clobber. flock is not reentrant across
        # fds in one process, so the sink must NOT re-take it per flush in that
        # mode -- it would deadlock against the lock the caller already holds.
        self._external_lock_held = external_lock_held
        # Dedup state: seeded from jobs.csv + the archive table, then GROWN as
        # rows are appended this run. A hit means the row is already on disk (or
        # was just written by an earlier source) -> "known". The Apple wave
        # dedups against phase-1's appended rows through this same set, so a
        # cross-source duplicate is phase-1-wins and counts as known (deliberate;
        # the row genuinely is in the csv by then). Intra-source exact duplicates
        # (one source's list repeating a row) are caught per-call and counted as
        # the funnel's "other", matching the old _per_source_funnel.
        self._known_urls = known_urls
        self._known_keys = known_keys
        self._plugin = plugin
        # Rows that were already in jobs.csv before this run, as raw CSV dicts.
        # ``flush`` rewrites the WHOLE file, so it must write these back first
        # (field-for-field, re-serialized -- never lifted through EnrichedJob,
        # so hand-edited cells round-trip untouched) or every pre-existing row
        # would be wiped. The run path captures them under the same initial
        # lock that seeds the dedup state; backfill leaves this empty (its
        # ``rows`` holds the whole file).
        self.preexisting_rows: list[dict[str, str]] = list(preexisting_rows or [])
        # Rows this run re-saw in a scrape (the deduped "known" branch): identity
        # (_csv_row_identity-compatible -- stripped URL, else (company, role) key)
        # -> the scraped job dict. Consumed once by ``_apply_rescan_updates`` in
        # ``flush`` to refresh Date Verified and heal a missing description on the
        # carried pre-existing row. Empty for backfill (it never scrapes).
        self._reseen: dict[str, dict[str, str]] = {}
        # URLs that already carried a non-empty sidecar description at run start.
        # ``rescan_summary`` uses this to count how many re-sightings actually
        # HEAL a gap (fill-only) independently of when the persisting flush runs,
        # so the scrape-phase summary is accurate even before the final flush and
        # on --no-enrich runs.
        self._original_desc_urls: set[str] = {
            url for url, text in self._descriptions.items() if (text or "").strip()
        }
        # Master list every enricher mutates in place; also the flush source.
        self.rows: list[EnrichedJob] = []
        # Never-enriched backlog rows this run re-enriches in place. Kept OFF
        # ``rows``: routing them there would append them at the bottom of
        # jobs.csv (reorder) and inflate ``written``/new_jobs. Must be THE
        # SAME list object the backlog wave mutates via slot replacement so
        # flush_periodic observes in-flight results (cf. backfill's
        # ``sink.rows = jobs``). Landed at flush by _apply_folded_updates:
        # enrichment-owned cells only, position preserved.
        self.folded_rows: list[EnrichedJob] = []
        # Unknown / hand-added columns (e.g. a user's "Priority"), one dict per
        # row in ``rows``, aligned POSITIONALLY and 1:1. Positional (not Link-
        # keyed) carry-through is the only lossless identity: an empty-Link row
        # keeps its own extras, and two rows sharing a Link keep their DISTINCT
        # extras. The run path appends an empty dict per appended row (canonical-
        # only); backfill preloads one per pre-existing row. Enrichment replaces
        # row slots in place and never adds/drops rows, so this stays aligned.
        self.row_extras: list[dict[str, str]] = []
        # Ordered non-canonical column labels, appended after the canonical
        # header on rewrite. Empty for the run path (scraped rows are canonical);
        # set by backfill from the stored file's header so column ORDER is stable.
        self.extra_columns: list[str] = []
        # Optional one-shot hook fired inside ``flush`` immediately before the
        # first ``atomic_write_rows``. backfill uses it to take the pre-mutation
        # backup LAZILY -- only when a write actually happens -- so a no-op
        # backfill (e.g. ollama down, nothing to persist) neither backs up nor
        # rewrites the file. ``_pre_write_done`` makes it fire exactly once.
        self.pre_write_hook: Callable[[], None] | None = None
        self._pre_write_done = False
        # When set (backfill), ``flush`` reads the current file and SKIPS the
        # write -- and so the lazy backup -- when the rendered rows are byte-for-
        # byte identical to what is already on disk. This is the no-churn
        # guarantee: an ollama-down / nothing-changed backfill must not rewrite
        # the file or drop a backup. The run path leaves this False -- it always
        # appends new rows, so its flush always has something to persist, and
        # paying a per-flush full-file read+compare there would be wasted work.
        self.skip_flush_if_unchanged = False
        # Serializes every disk-mutating section against every other one. An
        # append (extend rows + write the rows to disk) and a flush (snapshot +
        # rewrite the whole file) must each be atomic relative to the other:
        # during scrape/enrich overlap the Apple wave appends on the coordinator
        # thread while the wave-1 enrichment thread flushes. Held for the whole
        # critical section, NOT just the in-memory step -- a flush interleaving
        # between an append's list-extend and its file-write would otherwise
        # duplicate or drop rows on disk. Order is always this lock then the
        # cross-process ``file_lock``, everywhere. Slot replacement
        # (out[i] = job) inside an enricher is GIL-atomic and only ever touches
        # an index that wave already owns, so it needs no lock.
        self._rows_lock = threading.Lock()
        # Per-source funnel accumulated at append time (mirrors _per_source_funnel).
        self.funnel: dict[str, dict[str, int]] = {}
        # Run totals for the reconciling Completed line.
        self.raw_found = 0
        # URLs of previously VERIFICATION-CLOSED archived rows (url -> closed
        # date). A re-discovered watch URL is a reopened job: appended normally
        # but announced loudly, never silently slipped back in.
        self.reopen_watch: dict[str, str] = {}
        self.reopened = 0
        self.pre_filter = 0  # new (deduped, url-bearing, pre-location) survivors
        self.loc_filtered = 0
        # Sticky: a periodic flush hit an OSError and the rows it would have
        # persisted live only in memory. Enrichment keeps going (the in-memory
        # state is intact and the final flush retries), but run() warns at the
        # end so a silent disk problem can't masquerade as a healthy run.
        self.persistence_degraded = False
        self._degraded_reason = ""

    def _disk_lock(self) -> AbstractContextManager[None]:
        """The cross-process lock to take around a disk-mutating section.

        When the caller already holds the sentinel ``file_lock`` for the whole
        lifecycle (backfill), this is a no-op so the inner section does not
        re-acquire a non-reentrant flock and deadlock. Otherwise (run) it is the
        real per-section ``file_lock``.
        """
        if self._external_lock_held:
            return nullcontext()
        # Notice-only (no timeout): a mid-run flush must never abort a healthy
        # run on a slow peer. The non-blocking-first acquire keeps the notice
        # silent unless a flush genuinely contends with another command.
        return file_lock(self.lock_path, on_contention=workspace_busy_notice)

    def append_source(
        self, source_id: str, jobs: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Dedup/filter/lift one source's rows, append them, return its funnel.

        Returns the per-source counts (found/new/known/loc_skip). The dedup-set
        growth, the funnel/totals, and the master ``rows`` list are committed
        ONLY after the file append succeeds, so the in-memory state never claims
        rows that aren't on disk (no phantom dedup entries). A file-write failure
        leaves all state untouched and raises ``ScraperError`` for the caller to
        isolate as a failed source.

        Safe to call repeatedly for the SAME ``source_id``: the funnel/totals
        accumulate (``setdefault`` + ``+=``) and the dedup sets grow, so the slow
        jobspy-backed sources checkpoint each finished (term x country) unit by
        calling this per unit. Later units dedup against earlier ones through the
        grown ``_known_urls`` / ``_known_keys``, so an intra-source duplicate
        spanning units is counted "known", never double-appended.

        Thread-safe for concurrent callers: the WHOLE body (dedup-set reads,
        classification, the file write, and every commit) runs under
        ``_rows_lock``, so a jobspy worker thread checkpointing a unit and the
        coordinator appending another source serialize cleanly. The disk lock is
        taken inside, around the write only (lock order is always _rows_lock then
        file_lock, matching :meth:`flush`).
        """
        from daily_driver.plugins.job_search.scraper.csv_io import append_jobs_typed

        with self._rows_lock:
            counts = self.funnel.setdefault(
                source_id, {"found": 0, "new": 0, "known": 0, "loc_skip": 0}
            )
            # Classify the source's rows into a deferred-commit plan WITHOUT
            # mutating any shared state yet. ``found`` is the only counter touched
            # here (the raw scrape tally; it survives a write failure); every
            # classified count and dedup-set growth is applied below, after the
            # write lands.
            counts["found"] += len(jobs)
            self.raw_found += len(jobs)
            # Intra-source dedup: a single source's list may repeat a row (e.g.
            # Apple's locale loop). Those are the funnel's "other" -- found but not
            # classified -- so detect them here without counting them as "known".
            seen_urls: set[str] = set()
            seen_keys: set[str] = set()
            to_append: list[EnrichedJob] = []
            commit_urls: list[str] = []
            commit_keys: list[str] = []
            known = 0
            loc_skip = 0
            new = 0
            for job in jobs:
                # Strip the url at the dedup boundary so the in-memory known-set
                # key matches the stripped value RawScrapedJob writes to disk and
                # load_existing_jobs seeds back -- otherwise a padded url dedups
                # unstripped here but stripped on disk, re-appearing as a dup.
                url = (job.get("url") or "").strip()
                key = dedup_key(job.get("company", ""), job.get("role", ""))
                if (url and url in seen_urls) or (key and key in seen_keys):
                    continue  # within-call duplicate -> "other"
                if url:
                    seen_urls.add(url)
                if key:
                    seen_keys.add(key)
                if (url and url in self._known_urls) or (
                    key and key in self._known_keys
                ):
                    known += 1
                    # Record the re-sighting so ``_apply_rescan_updates`` can act
                    # on the carried row at flush. Prefer a sighting that carries
                    # a description: within one run the same URL may re-appear
                    # from a unit that lacks the body, so don't let it clobber an
                    # earlier sighting that had one.
                    identity = url or key
                    if identity:
                        prev = self._reseen.get(identity)
                        has_desc = bool((job.get("description_text") or "").strip())
                        prev_has_desc = bool(
                            (prev or {}).get("description_text", "").strip()
                        )
                        if prev is None or (has_desc and not prev_has_desc):
                            self._reseen[identity] = job
                    continue
                if not url:
                    # url-less new jobs cannot be deduped on future runs; drop them.
                    log.warning(
                        "Dropping job with no URL (cannot dedup on future runs): %s",
                        job.get("company", ""),
                    )
                    continue
                if not location_matches(job, self._plugin):
                    loc_skip += 1
                    continue
                new += 1
                commit_urls.append(url)
                if key:
                    commit_keys.append(key)
                to_append.append(_enriched_from_scraped(job))

            # A watch-listed URL belongs to a job that verification once
            # closed: announce the reopen loudly (it still appends below).
            for enriched_job in to_append:
                closed_on = self.reopen_watch.get(enriched_job.url)
                if closed_on is not None:
                    self.reopened += 1
                    log.warning(
                        "Reopened (previously closed %s): %s -- %s %s",
                        closed_on or "unknown date",
                        enriched_job.company,
                        enriched_job.role,
                        enriched_job.url,
                    )

            # Fold any scrape-time descriptions (e.g. JobSpy's LinkedIn body)
            # into the sidecar store so flush persists them alongside the row.
            for enriched_job in to_append:
                if (
                    enriched_job.url
                    and enriched_job.description_text
                    and self._descriptions.get(enriched_job.url)
                    != enriched_job.description_text
                ):
                    self._descriptions[enriched_job.url] = enriched_job.description_text
                    self._descriptions_dirty = True

            if to_append:
                # Write to disk first, then commit the in-memory state, all under
                # the held _rows_lock so a concurrent flush can't interleave
                # between extend and write (which would duplicate/drop rows on
                # disk). append_jobs_typed raises ScraperError on an OSError, which
                # propagates here BEFORE the rows.extend and the dedup/funnel
                # commit below -- so a write failure rolls back to nothing
                # committed and the caller isolates the source.
                with self._disk_lock():
                    append_jobs_typed(self.csv_path, to_append, self.header)
                self.rows.extend(to_append)
                # Newly scraped rows carry only canonical columns; keep the
                # extras list aligned 1:1 so a later flush merges by index.
                self.row_extras.extend({} for _ in to_append)
            # Commit dedup state + funnel/totals only now that the rows are durable.
            self._known_urls.update(commit_urls)
            self._known_keys.update(commit_keys)
            counts["known"] += known
            counts["loc_skip"] += loc_skip
            counts["new"] += new
            self.loc_filtered += loc_skip
            self.pre_filter += new
            return dict(counts)

    def _apply_rescan_updates(self, leading_rows: list[dict[str, str]]) -> None:
        """Apply updates to rows we re-saw this run (deduped 'known' rows).

        The single seam for on-rescan behavior -- add/remove actions here.
        Backfill never scrapes, so ``_reseen`` is empty and this is a no-op.
        """
        if not self._reseen:
            return
        stamp = today().isoformat()
        for row in leading_rows:
            identity = _csv_row_identity(row)
            job = self._reseen.get(identity)
            if job is None:
                continue
            # Freshness: a re-sighting is affirmative liveness evidence (drives
            # stale detection / prune). Log only on an actual change so periodic
            # + final flushes don't repeat the line for the same row.
            if row.get("Date Verified") != stamp:
                log.info("Re-seen, Date Verified -> %s: %s", stamp, identity)
                row["Date Verified"] = stamp
            # Heal a missing sidecar description (Indeed etc. carry it only at
            # scrape; the detail enricher is bot-walled). Fill-only; never
            # overwrite an existing entry (a re-scrape may be truncated).
            url = (row.get("Link") or "").strip()
            if url:
                desc = (job.get("description_text") or "").strip()
                if desc and not self._descriptions.get(url, "").strip():
                    self._descriptions[url] = desc
                    self._descriptions_dirty = True
                    log.info(
                        "Healed missing description (%d chars): %s", len(desc), url
                    )

    def _apply_folded_updates(self, leading_rows: list[dict[str, str]]) -> None:
        """Land backlog-wave enrichment on the matching leading rows in place.

        Writes ONLY the enrichment-owned cells: leading_rows is a fresh disk
        re-read, so a full-row overwrite from the run-start snapshot would
        revert any concurrent hand-edit (e.g. a Status flip mid-run). Rows are
        matched by identity and iterated row-side (like _apply_rescan_updates)
        so duplicate-identity rows all update. A folded row whose identity
        vanished from disk (concurrent prune) matches nothing -- its
        enrichment is discarded, never resurrected.
        """
        if not self.folded_rows:
            return
        by_identity: dict[str, dict[str, str]] = {}
        for job in self.folded_rows:
            csv_row = job.to_csv_row()
            by_identity[_csv_row_identity(csv_row)] = csv_row
        for row in leading_rows:
            src = by_identity.get(_csv_row_identity(row))
            if src is None:
                continue
            for cell in _ENRICHMENT_OWNED_CELLS:
                row[cell] = src[cell]

    def has_resightings(self) -> bool:
        """True when this run re-saw a known row.

        A --no-enrich run makes no final rewrite, so it must flush explicitly to
        persist the refreshed Date Verified / healed description this signals.
        """
        return bool(self._reseen)

    def rescan_summary(self) -> tuple[int, int, int]:
        """Freshness split for the scraping summary, independent of flush timing.

        Returns ``(still_visible, not_seen, healed)``: pre-existing rows the
        scrape re-confirmed live, pre-existing rows it did not re-return, and
        re-sightings that fill a previously-empty sidecar description. Derived
        from the re-sighting collector and the run-start pre-existing rows, so it
        is accurate before the persisting flush runs (and for --no-enrich runs).
        """
        still_visible = 0
        not_seen = 0
        healed = 0
        for row in self.preexisting_rows:
            job = self._reseen.get(_csv_row_identity(row))
            if job is None:
                not_seen += 1
                continue
            still_visible += 1
            url = (row.get("Link") or "").strip()
            if (
                url
                and url not in self._original_desc_urls
                and (job.get("description_text") or "").strip()
            ):
                healed += 1
        return still_visible, not_seen, healed

    def flush(self) -> None:
        """Rewrite jobs.csv -- carried non-run rows then ``rows`` -- through the
        one backfill rewrite path.

        Snapshot AND rewrite happen under one held ``_rows_lock`` + ``file_lock``
        so the whole flush is atomic relative to an append (no interleave between
        reading the rows and writing them out). The locks are held only for the
        rewrite, never across the slow LLM calls that produced the updates, so a
        concurrent prune/backfill is serialized but not starved.

        On the run path the sentinel lock was released for the long scrape/enrich
        phase, so the rows that existed at run start may have changed since. Each
        flush therefore RE-READS the current on-disk rows under the held lock and
        keeps the ones that are not this run's -- so a row a concurrent ``prune``
        or hand-edit removed stays removed and a field edit this run did not make
        survives -- then layers this run's enriched rows on top by identity (Link
        URL, falling back to the ``(company, role)`` dedup key). backfill keeps the
        sentinel lock for its whole lifecycle, so it replays its in-memory
        ``preexisting_rows`` directly without a re-read.

        Unknown / hand-added columns are carried through POSITIONALLY via
        ``row_extras`` (aligned 1:1 with ``rows``), not keyed by Link: every row
        keeps exactly ITS OWN extras even when its Link is blank or shared with
        another row.

        Raises ``OSError`` on a write failure -- the caller decides whether to
        degrade (periodic flush) or propagate (final flush). See
        :meth:`flush_periodic`.
        """
        from daily_driver.plugins.job_search.scraper.csv_io import (
            atomic_write_rows,
            read_rows,
        )
        from daily_driver.plugins.job_search.scraper.descriptions import (
            atomic_write_descriptions,
        )

        with self._rows_lock, self._disk_lock():
            snapshot = list(self.rows)
            extras_snapshot = list(self.row_extras)
            out_header = self.header + self.extra_columns
            if self._external_lock_held:
                # backfill holds the sentinel lock for its whole lifecycle, so the
                # ``preexisting_rows`` snapshot can't have gone stale under it.
                leading_rows: list[dict[str, str]] = list(self.preexisting_rows)
            else:
                # run path: the sentinel lock was RELEASED for the minutes-long
                # scrape/enrich phase, so the snapshot captured at run start may be
                # stale -- a concurrent prune/hand-edit could have removed or
                # changed rows since. Re-read the current on-disk rows under the
                # held flush lock and keep every row that is NOT one of this run's
                # rows. A row a concurrent writer removed stays removed; a field
                # edit this run did not make survives; this run's enriched rows are
                # layered on top below by append order. (The run's own appended
                # rows are already on disk -- exclude them by identity so the
                # in-memory enriched version wins instead of being duplicated.)
                _, on_disk_rows = read_rows(self.csv_path)
                run_identities = {
                    ident
                    for job in snapshot
                    if (ident := _csv_row_identity(job.to_csv_row()))
                }
                leading_rows = [
                    row
                    for row in on_disk_rows
                    if _csv_row_identity(row) not in run_identities
                ]
            # Act on rows we re-saw this run BEFORE the no-churn comparison, so a
            # re-seen-only run (Date Verified bumped, no new rows) is detected as
            # changed and rewrites jobs.csv. Mutates ``leading_rows`` in place
            # under the held ``_rows_lock``. The two update seams touch
            # disjoint cells (folded: enrichment-owned; rescan: Date Verified
            # + description heal), so their order is not load-bearing.
            self._apply_folded_updates(leading_rows)
            self._apply_rescan_updates(leading_rows)
            # Carried rows lead, field-for-field; this run's rows follow in append
            # order, so the rewrite preserves the on-disk layout.
            out_rows: list[dict[str, str]] = list(leading_rows)
            for i, job in enumerate(snapshot):
                csv_row = job.to_csv_row()
                if i < len(extras_snapshot):
                    csv_row.update(extras_snapshot[i])
                out_rows.append(csv_row)
            if self.skip_flush_if_unchanged:
                # No-churn: skip the write (and the lazy backup) when the file
                # already holds exactly these rows. read_rows returns only the
                # cells; compare against the same projection of out_rows.
                cur_header, cur_rows = read_rows(self.csv_path)
                projected = [
                    {c: row.get(c, "") for c in out_header} for row in out_rows
                ]
                if cur_header == out_header and cur_rows == projected:
                    return
            # Fire the one-shot pre-write hook (backfill's lazy backup) BEFORE the
            # write, inside the lock, so the backup snapshots the on-disk state
            # that this write is about to replace.
            if self.pre_write_hook is not None and not self._pre_write_done:
                self._pre_write_done = True
                self.pre_write_hook()
            atomic_write_rows(self.csv_path, out_header, out_rows)
            # Detail enrichment sets description_text via with_updates directly
            # on sink.rows, never through append_source -- fold the snapshot's
            # current descriptions in here so flush is the one place that
            # reliably observes every description, however it was filled.
            for enriched_job in snapshot:
                if (
                    enriched_job.url
                    and enriched_job.description_text
                    and self._descriptions.get(enriched_job.url)
                    != enriched_job.description_text
                ):
                    self._descriptions[enriched_job.url] = enriched_job.description_text
                    self._descriptions_dirty = True
            if self._descriptions_dirty:
                atomic_write_descriptions(self.csv_path, self._descriptions)
                self._descriptions_dirty = False

    def flush_periodic(self) -> None:
        """Best-effort flush for the in-loop resilience hook.

        A periodic flush that fails must NOT abort enrichment -- the in-memory
        state is intact and the final flush will retry. Instead it sets the
        sticky ``persistence_degraded`` flag (logged once at WARN as a
        PERSISTENCE failure, distinct from an enrichment failure) so run() can
        surface it at the end. Distinguishing this from an enrichment failure is
        the whole point: repeated disk errors must not masquerade as a healthy
        run while hours of enrichment live only in memory.
        """
        try:
            self.flush()
        except OSError as exc:
            self._degraded_reason = str(exc)
            if not self.persistence_degraded:
                self.persistence_degraded = True
                log.warning(
                    "PERSISTENCE failure: periodic save of %s failed (%s); "
                    "enrichment continues in memory and the final save will "
                    "retry",
                    self.csv_path,
                    exc,
                )


def run_all_scrapers(
    ctx: ScrapeContext,
    *,
    sources_override: list[str] | None = None,
    on_source_done: Callable[[str, bool, str], None] | None = None,
    on_source_start: Callable[[str], None] | None = None,
    on_source_progress: Callable[[str, int, int | None], None] | None = None,
    on_sources_enabled: Callable[[list[str]], None] | None = None,
    on_note: Callable[[str], None] | None = None,
    on_source_result: Callable[[str, list[dict[str, Any]]], object] | None = None,
    on_source_checkpoint: Callable[[str, list[dict[str, Any]]], object] | None = None,
    on_source_degraded: Callable[[str, str], None] | None = None,
    on_phase1_done: Callable[[bool], None] | None = None,
    force_headless: bool = False,
) -> tuple[
    list[dict[str, Any]], list[str], list[tuple[str, list[dict[str, Any]] | Exception]]
]:
    """Run all enabled scrapers and deduplicate results within this run.

    Two phases:
      Phase 1 — headless-safe sources run in parallel via ThreadPoolExecutor
      Phase 2 — non-headless sources (apple) run serially

    Phase 2 stays serial by design: running multiple visible Firefox windows
    concurrently is RAM-heavy and makes bot detection easier. Deduplicates by
    both URL and company+role key so the same job appearing on multiple boards
    is only kept once (first scraper wins).

    When ``sources_override`` is provided, only those source IDs run regardless
    of the ``sources`` toggles in config. Caller is responsible for
    validating IDs against ``SCRAPERS``.

    ``force_headless`` overrides the serial phase to run headless. The visible
    Firefox is the one uncontrolled terminal writer during the run; when a live
    progress block is pinned it can't share the terminal with a browser window,
    so the caller forces headless to keep the block clean.
    """
    cfg = ctx.plugin.scraper
    source_cfg = ctx.plugin.sources
    workers = cfg.parallel_workers

    if sources_override is not None:
        override = set(sources_override)
        enabled = [sid for sid in SCRAPERS if sid in override]
        disabled = [sid for sid in SCRAPERS if sid not in override]
        log.info("[--sources] running only: %s", ", ".join(enabled) or "(none)")
    else:

        def _is_enabled(sid: str) -> bool:
            toggle = source_cfg.get(sid)
            return toggle.enabled if toggle is not None else False

        enabled = [sid for sid in SCRAPERS if _is_enabled(sid)]
        disabled = [sid for sid in SCRAPERS if not _is_enabled(sid)]
    for sid in disabled:
        log.debug("[%s] disabled in config, skipping", sid)

    non_headless = _PLAYWRIGHT_SOURCES
    jobspy_enabled = [sid for sid in enabled if sid in _JOBSPY_SITES]
    headless_sources = [
        sid for sid in enabled if sid not in non_headless and sid not in _JOBSPY_SITES
    ]
    visible_sources = [sid for sid in enabled if sid in non_headless]

    # One scrape-plan row per enabled jobspy-backed site. Each row is a phase-1
    # work item bound to a scrape over its (single-site) list, so each site keeps
    # its own progress row, retry, and failure isolation.
    jobspy_plan = _jobspy_scrape_plan(jobspy_enabled, ctx.plugin)

    # Phase-1 work items: (row_id, scraper_fn). A None scraper_fn means look the
    # row id up in SCRAPERS; the jobspy rows carry an explicit bound scrape so a
    # site row id resolves to its own scrape without a registry entry.
    headless_items: list[
        tuple[str, Callable[[ScrapeContext], list[dict[str, Any]]] | None]
    ] = [(sid, None) for sid in headless_sources]
    for row_id, sites in jobspy_plan:

        def _jobspy_call(
            scrape_ctx: ScrapeContext, _sites: list[str] = sites
        ) -> list[dict[str, Any]]:
            return scrape_jobspy(scrape_ctx, sites=_sites)

        headless_items.append((row_id, _jobspy_call))

    # Announce the full run order up front so the display can list every source
    # as pending before any of them start.
    if on_sources_enabled is not None:
        on_sources_enabled([rid for rid, _ in headless_items] + visible_sources)

    # python-jobspy attaches its own stderr-bound log handlers. Reroute them
    # through our counting handler here -- single-threaded, before the parallel
    # phase spawns any worker -- so their WARN+ lines count toward the run's
    # Warnings: N total and read in our format, and stay silent in normal mode.
    # (Live per-source progress comes from ctx.report, not the library's logs.)
    if jobspy_enabled:
        try:
            import jobspy  # noqa: F401  -- create the import-time JobSpy:* loggers
        except ImportError:
            pass
        else:
            # jobspy logs "finished scraping" at scrape time via
            # create_logger(site.value.capitalize()) -- a name that differs from
            # its import-time module logger (e.g. "Linkedin" vs "LinkedIn") and
            # so isn't adopted yet. Force the runtime-named loggers to exist now
            # so adoption attaches our handler first; jobspy's create_logger then
            # won't add its own stderr handler (it only adds when none exist).
            for site in ("Linkedin", "Indeed"):
                logging.getLogger(f"JobSpy:{site}")
            adopt_third_party_loggers("JobSpy")

        # Explain the per-site query count once, here in the single-threaded
        # setup -- not inside the worker, which would race enlighten's cursor.
        # Each site searches every (search term x country) pair.
        if on_note is not None:
            from daily_driver.plugins.job_search.scraper.roles import _search_terms

            terms = _search_terms(ctx.plugin)
            countries = countries_list(ctx.plugin)
            on_note(
                f"{len(terms)} search terms x {len(countries)} countries "
                f"= {len(terms) * len(countries)} searches per site"
            )

    results: list[tuple[str, list[dict[str, Any]] | Exception]] = []

    # Sources that checkpoint per unit (the slow jobspy-backed ones) append
    # incrementally as they run, so the coordinator must SKIP their end-of-source
    # append (else every checkpointed row would be appended twice). The wrapper
    # records the source id the first time it checkpoints anything; the sink's
    # append_source dedups within a call, but a second whole-source append would
    # re-classify already-known rows as "known" and skew the funnel, so skip it
    # outright.
    checkpointed_sources: set[str] = set()

    def _checkpoint(sid: str, batch: list[dict[str, Any]]) -> None:
        checkpointed_sources.add(sid)
        if on_source_checkpoint is not None:
            on_source_checkpoint(sid, batch)

    # Phase 1: headless, parallel
    if headless_items:
        headless_ctx = _ctx_with_headless(ctx, True)
        log.info(
            "[phase1] running %d headless scrapers (%s), %d workers",
            len(headless_items),
            ", ".join(rid for rid, _ in headless_items),
            workers,
        )
        pool = ThreadPoolExecutor(max_workers=max(1, workers))
        # Track which futures still need collecting so the first-interrupt drain
        # can resume from where the normal loop left off (no double-processing).
        pending: set[Any] = set()

        def _collect(fut: Any) -> None:
            sid = futures[fut]
            pending.discard(fut)
            result = fut.result()
            results.append((sid, result))
            # Append-as-completed: hand each successful source straight to the
            # sink on this (coordinator) thread so its rows reach jobs.csv before
            # the next source finishes. A crash mid-phase then loses only the
            # in-flight source's current unit. A source that already checkpointed
            # per unit is fully appended -- skip its end-of-source append.
            if (
                on_source_result is not None
                and not isinstance(result, Exception)
                and sid not in checkpointed_sources
            ):
                on_source_result(sid, result)

        # Only the jobspy-backed (multi-hour) sites checkpoint per unit; the fast
        # headless sources rely on the end-of-source append (seconds of loss).
        def _checkpoint_for(
            row_id: str,
        ) -> Callable[[str, list[dict[str, Any]]], None] | None:
            return _checkpoint if row_id in _JOBSPY_SITES else None

        try:
            futures = {
                pool.submit(
                    _run_one,
                    row_id,
                    headless_ctx,
                    on_source_done,
                    on_source_start,
                    on_source_progress,
                    fn,
                    _checkpoint_for(row_id),
                    on_source_degraded,
                ): row_id
                for row_id, fn in headless_items
            }
            pending = set(futures)
            for fut in as_completed(futures):
                _collect(fut)
        except KeyboardInterrupt:
            # First Ctrl-C/SIGTERM during scraping: stop gracefully, KEEP what is
            # already fetched. Set the cooperative stop flag so each still-running
            # source returns the jobs it has accumulated at its next unit boundary
            # (in-flight HTTP within a unit runs to its own timeout). Cancel the
            # unstarted futures, then DRAIN the started ones -- routing their
            # partial results through the sink -- before re-raising so the run is
            # marked interrupted at phase=scraping. A SECOND interrupt during the
            # drain is the escape hatch: abandon the pool wait and re-raise now.
            ctx.stop_event.set()
            # The graceful drain can take minutes (an in-flight jobspy unit runs
            # to its own timeout), during which a bare Ctrl-C looks like a no-op
            # and the user is tempted to press it again -- which hits the abort
            # path and loses the drain. Surface what is happening, above the live
            # bars (on_note routes to rp.note), so the wait reads as deliberate.
            if on_note is not None:
                on_note(
                    "Interrupted -- finishing in-flight searches and saving; "
                    "press Ctrl-C again to abort now"
                )
            for fut in pending:
                fut.cancel()
            try:
                for fut in as_completed(pending):
                    if fut.cancelled():
                        continue
                    _collect(fut)
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            pool.shutdown(wait=True)
            raise
        else:
            pool.shutdown(wait=True)

    # Phase 1 results are all appended; signal the caller so it can start
    # enriching them while phase 2 (Apple) still scrapes (overlap). The flag
    # tells the caller whether a phase 2 actually follows -- only then is the
    # overlapped two-wave path worth the background thread.
    if on_phase1_done is not None:
        on_phase1_done(bool(visible_sources))

    # Phase 2: serial (preserves pre-parallel behavior). Visible browser by
    # default to dodge bot detection; forced headless via force_headless. Skipped
    # entirely when a phase-1 interrupt already requested a graceful stop -- the
    # appended phase-1 rows are kept and the run exits without starting Apple.
    if visible_sources and not ctx.stop_event.is_set():
        visible_ctx = _ctx_with_headless(ctx, force_headless)
        log.info(
            "[phase2] running %d %s scrapers serially (%s)",
            len(visible_sources),
            "headless" if force_headless else "non-headless",
            ", ".join(visible_sources),
        )
        for sid in visible_sources:
            # Apple runs on this (coordinator) thread, so a Ctrl-C/SIGTERM lands
            # in its call stack directly. Apple catches the interrupt internally,
            # sets the stop flag, and returns the jobs it accumulated so far; we
            # append those, then stop the serial loop and re-raise so the run is
            # marked interrupted at phase=scraping.
            result = _run_one(
                sid,
                visible_ctx,
                on_source_done,
                on_source_start,
                on_source_progress,
                on_source_degraded=on_source_degraded,
            )
            results.append((sid, result))
            if on_source_result is not None and not isinstance(result, Exception):
                on_source_result(sid, result)
            if ctx.stop_event.is_set():
                raise KeyboardInterrupt

    all_jobs, failed_sources = _merge_and_dedup(results)
    return all_jobs, failed_sources, results


# ── Ollama enrichment preflight ──────────────────────────────────────────────


# Independent of the configured per-call enrich_timeout: this single reachability
# probe should fail fast on a down server, not wait out a tuning value meant for
# real generation calls.
_OLLAMA_PREFLIGHT_TIMEOUT = 3

# Bound on the interrupt-path join of the overlap (wave-1) enrichment thread.
# Long enough to let an in-flight LLM call settle and flush, short enough that a
# second Ctrl-C is not swallowed; past it the daemon thread expires on its own
# enrich_timeout and the final flush proceeds with what it applied.
_WAVE1_INTERRUPT_JOIN_SECONDS = 30


def _llm_enrichment_requested(plugin: JobSearchPlugin) -> bool:
    """Whether this run will make ollama LLM calls (detail pages don't count).

    Detail-page enrichment is plain HTTP against the job board, so it needs no
    provider. Only the fit / notes pass routes to the LLM, so the preflight is
    warranted only when it is on.
    """
    cfg = plugin.enrichment
    return cfg.enrich_fit or cfg.enrich_notes


def _ollama_enrichment_preflight(plugin: JobSearchPlugin, ai: AIConfig) -> bool:
    """Ping ollama once before LLM enrichment; return False (and warn) to skip.

    Mirrors the claude which-guard intent — verify the provider is usable before
    the per-job loop — but for ollama the check is a network probe, so it runs
    here at run start rather than per enricher. One cheap ``list_models`` GET on
    a short fixed timeout (:data:`_OLLAMA_PREFLIGHT_TIMEOUT`) stands in for N
    per-call timeouts on a down server. Reuses the doctor reachability + pulled-
    model logic.

    Each enrichment phase routes independently (a global `ai.provider: ollama`
    reaches enrichment even with the enrichment block unset), so the resolved
    route per phase — not the raw `enrichment.provider` — decides whether to
    probe. Returns True (proceed) when no enabled phase resolves to ollama.
    """
    from daily_driver.integrations import ai_provider, ollama_client

    cfg = plugin.enrichment
    # A phase is probed only when its own toggles are on AND it resolves to
    # ollama: a disabled phase makes no LLM calls, so its route is moot.
    phase_enabled = {
        "fit_notes": cfg.enrich_fit or cfg.enrich_notes,
    }
    ollama_models: list[str] = []
    for phase, enabled in phase_enabled.items():
        if not enabled:
            continue
        provider, model = ai_provider.resolve_route(ai, task=phase, domain_cfg=cfg)
        if provider == "ollama":
            ollama_models.append(model or ollama_client.DEFAULT_MODEL)
    if not ollama_models:
        return True

    endpoint = ai.ollama.endpoint
    try:
        pulled = ollama_client.list_models(endpoint, timeout=_OLLAMA_PREFLIGHT_TIMEOUT)
    except ollama_client.OllamaNotReachableError:
        Console.warning(
            f"ollama not reachable at {endpoint} -- LLM enrichment skipped this "
            "run; start the server (ollama serve) and run jobs backfill to "
            "fill these rows"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        Console.warning(
            f"ollama at {endpoint} returned an error ({exc}) -- LLM enrichment "
            "skipped this run; run jobs backfill once it is healthy"
        )
        return False

    # Preserve insertion order while de-duping (both phases may share a model).
    missing = [m for m in dict.fromkeys(ollama_models) if m not in pulled]
    if missing:
        model = missing[0]
        Console.warning(
            f"ollama model {model!r} not pulled at {endpoint} -- LLM enrichment "
            f"skipped this run; pull it (ollama pull {model}) and run jobs "
            "backfill to fill these rows"
        )
        return False
    return True


# ── Public entry points ──────────────────────────────────────────────────────


def _backfill_needs(
    jobs: list[EnrichedJob],  # noqa: F821
    plugin: JobSearchPlugin,
    force: bool = False,
    cooldown_cutoff: datetime | None = None,
) -> dict[str, int]:
    """Per-phase counts of rows the enricher WOULD actually touch.

    Rows in an ENRICH_SKIP status are excluded (deliberately untouched). The
    counts mirror the enricher's own eligibility, not a re-implementation:

    - ``fit_notes`` uses :func:`_fit_notes_eligible` -- the combined predicate,
      since one fit/notes call fills both Fit and Notes. There is no separate
      notes count (a single call covers it), so the dry-run report and the
      nothing-to-do short-circuit speak in those terms. The count is gated on
      BOTH ``enrich_fit`` and ``enrich_notes``: the fit pass refuses to run
      unless both are on, so a disabled axis reports zero need rather than
      over-reporting work the pass would never do. Under ``force`` every active
      row counts (the enricher re-enriches and overwrites all of them).

    Used by the dry-run report and the start-of-run short-circuit.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import _active
    from daily_driver.plugins.job_search.scraper.enrichment.llm import (
        _fit_notes_eligible,
    )

    cfg = plugin.enrichment
    active = [j for j in jobs if _active(j)]
    fit_notes = (
        sum(
            1
            for j in active
            if _fit_notes_eligible(j, force=force, cooldown_cutoff=cooldown_cutoff)
        )
        if (cfg.enrich_fit and cfg.enrich_notes)
        else 0
    )
    return {
        "rows": len(active),
        "fit_notes": fit_notes,
    }


def run_backfill(
    plugin: JobSearchPlugin,
    csv_path: Path,
    ephemeral_dir: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
    dry_run: bool = False,
    limit: int | None = None,
    force: bool = False,
    cooldown_hours: int | str | None = None,
    emit_json: bool = False,
) -> dict[str, Any]:
    """Re-enrich empty fields in an existing jobs.csv via the modern driver.

    Shares the run-side enrichment machinery: ``_enrich_wave`` renders the same
    Detail pages / Fit and notes phase rows under a "Job backfill"
    :class:`RunProgress` block, fans the fit/notes pass out under the provider's
    concurrency cap, flushes every ~25 results, and runs the ollama preflight
    before the LLM phase.

    The enricher itself skips already-filled fields and ENRICH_SKIP-status
    rows, so handing the whole row list to the wave enriches only the empties.
    ``force`` (``--force-update``) instead re-enriches EVERY active row and
    OVERWRITES its Fit, Notes, and Remote, still bounded by ``limit`` and the
    fit budget cap.

    ``limit`` caps LLM spend this invocation by bounding the fit budget at
    ``limit`` (``None`` = the config cap; the CLI rejects ``limit < 1``).
    ``dry_run`` reports the would-enrich count and makes zero LLM/detail calls
    and zero writes (no backup either).

    Locking: the sentinel ``file_lock`` is held for the WHOLE lifecycle -- the
    read of jobs.csv, the enrichment, and every rewrite all happen inside it.
    backfill rewrites the file from its in-memory snapshot, so reading or
    rewriting outside the lock would let a concurrent run's append slip into the
    window and be clobbered. The sink is built with ``external_lock_held=True``
    so its per-flush sections do not re-take the (non-reentrant) flock.

    The pre-mutation backup is LAZY: taken just before the first actual write
    (the sink's ``pre_write_hook``), so a no-op backfill (ollama down, nothing to
    persist) leaves the file untouched and writes no backup. The final rewrite is
    skipped entirely when enrichment changed no row content.

    Returns a completion summary dict (rows considered, skipped, Fit/Notes
    needed-before/after, enriched delta, elapsed seconds) so the CLI can wrap it
    in the standard ``{schema, data}`` JSON envelope. ``emit_json`` suppresses
    the human Console summary lines and forces plain (no live-bar) progress so
    ``--json`` keeps stdout clean.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        _make_backup,
        format_canonicalized_notice,
        read_rows,
    )
    from daily_driver.plugins.job_search.scraper.descriptions import (
        gc_descriptions,
        load_descriptions,
    )
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob

    if not csv_path.exists():
        raise ScraperError(f"jobs.csv not found at {csv_path}")

    backfill_started_at = datetime.now(timezone.utc)
    ctx = ScrapeContext(plugin=plugin, ai=ai or AIConfig(), context_text=context_text)
    ai_cfg = ctx.ai
    clear_stale_adjacent_lock(csv_path)
    lock_path = jobs_lock_path(ephemeral_dir)

    # Force-update cooldown: skip rows enriched within the window so an
    # interrupted force-update resumes instead of restarting. CLI --cooldown-hours
    # overrides the config default; the cutoff is only meaningful under force.
    # `0` disables it (re-enrich every active row); `missing` re-enriches only
    # rows with no enrichment timestamp yet -- modelled as a cutoff before any
    # real timestamp, so every already-enriched row falls "within cooldown".
    resolved_cooldown: int | str = (
        cooldown_hours
        if cooldown_hours is not None
        else plugin.enrichment.force_recook_cooldown_hours
    )
    cooldown_cutoff: datetime | None
    if not force:
        cooldown_cutoff = None
    elif resolved_cooldown == "missing":
        cooldown_cutoff = datetime.min.replace(tzinfo=timezone.utc)
    elif isinstance(resolved_cooldown, int) and resolved_cooldown > 0:
        cooldown_cutoff = backfill_started_at - timedelta(hours=resolved_cooldown)
    else:  # 0 (or unexpected) -> cooldown disabled
        cooldown_cutoff = None

    # Dry-run reads outside the lock: it never writes, so a concurrent run can't
    # be clobbered by it. The real path reads INSIDE the lock (below).
    if dry_run:
        _, stored_rows = read_rows(csv_path)
        jobs = [EnrichedJob.from_csv_row(r) for r in stored_rows]
        store = load_descriptions(csv_path)
        jobs = [
            (
                job.with_updates(description_text=store[job.url])
                if not job.description_text and job.url in store
                else job
            )
            for job in jobs
        ]
        needs = _backfill_needs(
            jobs, plugin, force=force, cooldown_cutoff=cooldown_cutoff
        )
        skipped = len(jobs) - needs["rows"]
        enrich_cfg = plugin.enrichment
        fit_cap = limit if limit is not None else enrich_cfg.max_enrich_fit
        # Preview the sidecar GC without writing (dry-run mutates nothing).
        live_urls = {job.url for job in jobs if job.url}
        would_drop = sum(1 for url in store if url not in live_urls)
        if not emit_json:
            if would_drop:
                Console.info(
                    f"Backfill dry-run: would clean up {would_drop} orphaned "
                    "description(s). Nothing written."
                )
            verb = "re-enrich (overwrite)" if force else "need"
            Console.info(
                f"Backfill dry-run: {needs['fit_notes']} {verb} Fit/Notes "
                f"({needs['rows']} active rows, {skipped} skipped). Nothing written."
            )
            # The needs counts are the FULL backlog; one pass spends at most the
            # config caps (or --limit). Say so when a cap would trim this pass,
            # or the report reads as if the caps were ignored.
            if needs["fit_notes"] > fit_cap:
                source = "--limit" if limit is not None else "config"
                Console.info(
                    f"This pass would be fit/notes capped at {fit_cap} ({source}); "
                    "run backfill again to continue."
                )
        return {
            "dry_run": True,
            "rows": needs["rows"],
            "skipped": skipped,
            "needs_before": needs["fit_notes"],
            "needs_after": needs["fit_notes"],
            "enriched": 0,
            "fit_cap": fit_cap,
            "elapsed_seconds": None,
        }

    # No --limit: None lets the enrichment plan apply the config cap
    # (max_enrich_fit).
    fit_budget = limit

    tty = Console.live_progress_enabled(suppress=emit_json)
    # Announce-then-wait on contention, with a bounded give-up so a wedged peer
    # can't hang backfill forever. ExitStack lets the give-up branch return
    # before the lock body without re-indenting the whole lifecycle block.
    lock_stack = ExitStack()
    try:
        lock_stack.enter_context(
            file_lock(
                lock_path,
                timeout=LOCK_WAIT_TIMEOUT_SECONDS,
                on_contention=workspace_busy_notice,
            )
        )
    except TimeoutError:
        raise ScraperError(LOCK_GIVEUP_MESSAGE) from None
    try:
        # READ INSIDE THE LOCK: the snapshot the rewrite is built from must be
        # consistent with the lock window, or a concurrent run's append between
        # an out-of-lock read and the rewrite would be lost.
        stored_header, stored_rows = read_rows(csv_path)
        jobs = [EnrichedJob.from_csv_row(r) for r in stored_rows]
        store = load_descriptions(csv_path)
        jobs = [
            (
                job.with_updates(description_text=store[job.url])
                if not job.description_text and job.url in store
                else job
            )
            for job in jobs
        ]
        # Reconcile the description sidecar against the live jobs.csv URLs read
        # above (archived rows never need descriptions -- the store is a pure
        # cache). Prune the in-memory store to match too, or the sink's later
        # dirty flush would re-persist the orphans this GC just dropped.
        live_urls = {job.url for job in jobs if job.url}
        dropped_descriptions = gc_descriptions(csv_path, live_urls, store=store)
        if dropped_descriptions:
            store = {url: text for url, text in store.items() if url in live_urls}
            if not emit_json:
                Console.info(
                    f"Cleaned up {dropped_descriptions} orphaned description(s)."
                )
        # Backfill lifts every stored row through from_csv_row, which normalizes
        # the Status spelling. That rewrites rows the user did not ask to touch,
        # so collect the spelling changes (old -> canonical) for a visible
        # notice once enrichment finishes. Meaning is never changed.
        status_canonicalizations: list[tuple[str, str]] = [
            (raw, job.status)
            for raw, job in (
                ((r.get("Status") or ""), j) for r, j in zip(stored_rows, jobs)
            )
            if raw and raw != job.status
        ]
        # Unknown / hand-added columns (e.g. a user's "Priority") are not modelled
        # by EnrichedJob; carry them through POSITIONALLY (the sink merges
        # row_extras by index, lossless for empty/duplicate Links). The sink gets
        # CANONICAL_HEADER plus the explicit extra-column ORDER from the file.
        extra_columns = [c for c in stored_header if c not in CANONICAL_HEADER]
        row_extras = [{c: r.get(c, "") for c in extra_columns} for r in stored_rows]
        if extra_columns:
            log.info(
                "[backfill] carrying through %d non-canonical column(s): %s",
                len(extra_columns),
                ", ".join(extra_columns),
            )

        needs = _backfill_needs(
            jobs, plugin, force=force, cooldown_cutoff=cooldown_cutoff
        )
        skipped = len(jobs) - needs["rows"]
        log.info(
            "[backfill] %d rows (%d skipped excluded): %d need Fit/Notes",
            len(jobs),
            skipped,
            needs["fit_notes"],
        )

        if not needs["fit_notes"]:
            # No backup, no rewrite: release the lock cleanly. (The lock context
            # exits normally when we return.)
            if not emit_json:
                Console.info("All rows already enriched, nothing to backfill.")
            elapsed = (datetime.now(timezone.utc) - backfill_started_at).total_seconds()
            return {
                "dry_run": False,
                "rows": len(jobs),
                "skipped": skipped,
                "needs_before": 0,
                "needs_after": 0,
                "enriched": 0,
                "elapsed_seconds": elapsed,
            }

        sink = _JobSink(
            csv_path=csv_path,
            lock_path=lock_path,
            header=CANONICAL_HEADER,
            known_urls=set(),
            known_keys=set(),
            plugin=plugin,
            external_lock_held=True,
            descriptions=store,
        )
        sink.rows = jobs
        sink.row_extras = row_extras
        sink.extra_columns = extra_columns
        # No-churn: every backfill flush (periodic and final) skips the write --
        # and the lazy backup -- when nothing actually changed on disk.
        sink.skip_flush_if_unchanged = True

        # Lazy backup: the sink fires this once, just before its first real write.
        backup_holder: list[Path] = []

        def _take_backup() -> None:
            backup = _make_backup(csv_path)
            backup_holder.append(backup)
            log.info("[backfill] backed up to %s", backup.name)

        sink.pre_write_hook = _take_backup

        # Captured from the enrichment wave below; reported after completion.
        fit_stats: dict[str, int] = _empty_fit_stats()

        try:
            with (
                live_log_window(tty),
                RunProgress(
                    Console.get_log_console(), tty=tty, title="Job backfill"
                ) as rp,
            ):
                # title + "Enriching jobs" header + the phase rows the wave will
                # render -- reserve the block in one scroll-region set, as run()
                # does, to avoid the per-bar resize gap. Phases: detail (always)
                # and fit/notes (when enabled). Must match the fit_enabled gate in
                # _enrich_wave or the reserved row count drifts from the phases
                # actually rendered.
                enrich_cfg = plugin.enrichment
                phase_rows = 1
                if enrich_cfg.enrich_fit and enrich_cfg.enrich_notes:
                    phase_rows += 1
                rp.reserve(2 + phase_rows)
                enrich_group = rp.group("Enriching jobs")
                _, fit_stats = _enrich_wave(
                    plugin,
                    ai_cfg,
                    jobs,
                    ctx,
                    sink,
                    enrich_group,
                    wave_label="",
                    run_preflight=True,
                    fit_budget=fit_budget,
                    force=force,
                    cooldown_cutoff=cooldown_cutoff,
                    capture_descriptions=False,
                )
                enrich_group.done()
        except KeyboardInterrupt as interrupt:
            # Persist whatever the interrupted wave produced; the final rewrite
            # still runs under the held lock. The original jobs.csv is intact if
            # the write itself fails (atomic rename only fires on success), and a
            # backup gives a clean rollback either way. A second Ctrl-C (or any
            # other error) during this teardown flush must NOT replace the
            # original interrupt and degrade exit 130 -> 1: log it and re-raise
            # the original. The lazy backup is taken INSIDE flush, so read
            # backup_holder AFTER it returns.
            try:
                sink.flush()
            except KeyboardInterrupt:
                log.warning("Second interrupt during backfill teardown flush")
            except Exception as exc:  # noqa: BLE001 -- never mask the original KI
                log.error("Backfill teardown flush failed: %s", exc)
            else:
                if backup_holder:
                    Console.warning(
                        f"Backfill interrupted: partial progress saved to "
                        f"jobs.csv (original preserved at {backup_holder[0].name})"
                    )
                else:
                    Console.warning("Backfill interrupted: no changes to save")
            raise interrupt
        else:
            # Parity with run(): if periodic saves degraded mid-run, say so once,
            # then let the final flush retry. The final flush is a no-op (no
            # write, no backup) when enrichment changed nothing -- e.g. ollama
            # down -- so a quiet run leaves the file's bytes and mtime untouched.
            # A final-flush OSError is caught here to name the backup before
            # re-raising, so the user has a rollback path even when the disk
            # write fails.
            if sink.persistence_degraded:
                Console.warning(
                    f"periodic saves failed during the backfill "
                    f"({sink._degraded_reason}); retrying the final save now"
                )
            try:
                sink.flush()
            except OSError as exc:
                backup_name = backup_holder[0].name if backup_holder else "(none taken)"
                Console.error(
                    f"Backfill final save failed ({exc}); original preserved "
                    f"at {backup_name}"
                )
                raise
    finally:
        lock_stack.close()

    after = _backfill_needs(jobs, plugin)
    elapsed = (datetime.now(timezone.utc) - backfill_started_at).total_seconds()
    enriched = needs["fit_notes"] - after["fit_notes"]
    if not emit_json:
        Console.success(
            f"Backfill complete: +{enriched} Fit/Notes. "
            f"Total run time: {_fmt_duration(elapsed)}."
        )
        # Backfill relies solely on the cached descriptions (descriptions.jsonl):
        # it never fetches over the network. A row with no cached description is
        # left un-scored (and gets no enrichment timestamp) rather than guessed
        # at; call it out so the phase-line count isn't mistaken for ordinary
        # skips. Descriptions are captured at scrape time; a row already in
        # jobs.csv is deduped by a re-run, so recovering it means deleting it so
        # the next scrape re-adds (and describes) it.
        no_description_count = fit_stats.get("no_description", 0)
        if no_description_count:
            Console.warning(
                f"{no_description_count} job(s) had no cached description; "
                "Fit/Notes left blank. Descriptions are captured during "
                "`jobs run`; delete a row so a re-scrape re-adds and describes it."
            )
        # The rewrite ran (this point is reached only past the no-enrichment early
        # return), so any status spellings it canonicalized are now on disk. Make
        # that visible, once, when something actually changed.
        canon_notice = format_canonicalized_notice(status_canonicalizations)
        if canon_notice is not None:
            Console.info(canon_notice)
    return {
        "dry_run": False,
        "rows": len(jobs),
        "skipped": skipped,
        "needs_before": needs["fit_notes"],
        "needs_after": after["fit_notes"],
        "enriched": enriched,
        "elapsed_seconds": elapsed,
    }


def _print_dry_run_table(jobs: list[EnrichedJob]) -> None:  # noqa: F821
    """Render a Rich table summary of dry-run matches."""
    from rich.console import Console
    from rich.table import Table

    from daily_driver.plugins.job_search.scraper.parsing import _fix_mojibake

    console = Console(stderr=False)
    if not jobs:
        console.print("[dim]Dry-run: no new jobs matched.[/dim]")
        return

    table = Table(
        title=f"Dry-run preview ({len(jobs)} new jobs)",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Source")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Location")
    table.add_column("URL", overflow="fold")
    for j in jobs:
        table.add_row(
            _fix_mojibake(j.source),
            _fix_mojibake(j.company),
            _fix_mojibake(j.role),
            _fix_mojibake(j.location),
            j.url,
        )
    console.print(table)
    console.print(
        f"[dim]{len(jobs)} new jobs (dry-run, nothing written).[/dim]",
    )


def _run_llm_enrichment(
    plugin: JobSearchPlugin,
    ai_cfg: AIConfig,
    typed_jobs: list[EnrichedJob],  # noqa: F821
    ctx: ScrapeContext,
    sink: _JobSink,
    fit_phase: Phase | None,
    *,
    run_preflight: bool,
    fit_budget: int | None = None,
    exclude_fit_urls: frozenset[str] = frozenset(),
    attempted: dict[str, set[str]] | None = None,
    force: bool = False,
    cooldown_cutoff: datetime | None = None,
) -> dict[str, int]:
    """Run the fit/notes LLM pass, flushing as it goes.

    Returns ``fn_stats``. ``run_preflight`` gates the ollama reachability probe
    -- run it before the FIRST wave only; later waves skip it (the server state
    cannot change mid-run). On a failed probe the LLM phase renders as a
    zero-count done bar and zero counters (the same shape ``--no-enrich``
    records), so the manifest stays honest and the user fills the rows later with
    ``jobs backfill``. The claude provider is untouched (its which-guard inside
    the enricher already covers a missing CLI). ``sink.flush_periodic`` is the
    periodic resilience hook, invoked every ~25 results on the coordinator
    thread. ``fit_budget`` is the SHARED running total's remaining allowance for
    this wave (None = the config cap; an explicit 0 spends nothing).
    """
    from daily_driver.plugins.job_search.scraper.enrichment import (
        enrich_fit_and_notes,
    )

    run_llm = True
    if run_preflight and _llm_enrichment_requested(plugin):
        run_llm = _ollama_enrichment_preflight(plugin, ai_cfg)

    if run_llm:
        # A None phase means the pass is disabled by config: no bar exists and
        # the plan builder skips the pass on its own toggles.
        if fit_phase is not None:
            fit_phase.start()
        _, fn_stats = enrich_fit_and_notes(
            typed_jobs,
            ctx,
            budget=fit_budget,
            progress=fit_phase.advance if fit_phase is not None else None,
            # The bar is pinned with total=len(jobs) before the plan exists; once
            # the planner resolves the budget-capped call count, re-base the
            # denominator so the bar shows what will actually be spent.
            on_planned=fit_phase.set_total if fit_phase is not None else None,
            # Periodic in-loop flush degrades (not crashes) on a disk error.
            flush=sink.flush_periodic,
            exclude_urls=exclude_fit_urls,
            attempted=attempted,
            force=force,
            cooldown_cutoff=cooldown_cutoff,
        )
    else:
        fn_stats = _empty_fit_stats()
    if fit_phase is not None:
        # .get() rather than a bare index: callers (tests, older stubs) may
        # return a stats dict without this key, and 0 is the correct default.
        fit_phase.done(
            f"{fn_stats['enriched']} enriched, "
            f"{fn_stats['skipped_budget']} skipped (budget), "
            f"{fn_stats.get('no_description', 0)} no description, "
            f"{fn_stats['failed']} failed"
        )
    return fn_stats


def _enrich_wave(
    plugin: JobSearchPlugin,
    ai_cfg: AIConfig,
    jobs: list[EnrichedJob],  # noqa: F821
    ctx: ScrapeContext,
    sink: _JobSink,
    enrich_group: Group,
    *,
    wave_label: str,
    run_preflight: bool,
    fit_budget: int | None,
    set_phase: Callable[[str], None] | None = None,
    exclude_fit_urls: frozenset[str] = frozenset(),
    attempted: dict[str, set[str]] | None = None,
    force: bool = False,
    cooldown_cutoff: datetime | None = None,
    capture_descriptions: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Run one enrichment wave (detail + LLM) over ``jobs`` in place.

    ``wave_label`` suffixes the phase rows ("" for the only/first wave, e.g.
    " (wave 2)" for the post-Apple wave) so the live display shows each wave's
    own phase rows -- the honest two-wave UI. ``fit_budget`` is this wave's
    remaining slice of the shared running total (None = the config cap; an
    explicit 0 spends nothing). ``set_phase`` records the coarse run phase for
    the manifest. ``force`` re-enriches and overwrites every active row's
    Fit/Notes/Remote (backfill --force-update). ``capture_descriptions`` False
    (the backfill path) keeps descriptions cache-only: the detail phase fills
    comp but not description_text, and a row with no cached description is left
    un-enriched by fit/notes rather than fetched. Flushes per phase and on the
    periodic hook; the caller's try/except flushes once more on interrupt.
    Returns ``(detail_stats, fn_stats)``.
    """
    from daily_driver.plugins.job_search.scraper.enrichment import enrich_job_details
    from daily_driver.plugins.job_search.scraper.enrichment.detail import (
        render_detail_summary,
    )

    total = len(jobs)
    detail_phase = enrich_group.phase(f"Detail pages{wave_label}", total=total)
    # A disabled pass renders NO bar: pinning one with a placeholder total for
    # work that will never run reads as a stuck/ignored toggle.
    enrich_cfg = plugin.enrichment
    fit_enabled = enrich_cfg.enrich_fit and enrich_cfg.enrich_notes
    fit_phase: Phase | None = None
    if fit_enabled:
        fit_phase = enrich_group.phase(f"Fit and notes{wave_label}", total=total)

    if set_phase is not None:
        set_phase("detail")
    detail_phase.start()
    _, detail_stats = enrich_job_details(
        jobs,
        ctx,
        progress=detail_phase.advance,
        capture_descriptions=capture_descriptions,
    )
    detail_phase.done(render_detail_summary(detail_stats))
    # Persist detail comp/posted_date before the LLM phase; a disk error here
    # degrades rather than aborting (the final flush retries and propagates).
    sink.flush_periodic()

    if set_phase is not None:
        set_phase("enrichment")

    fn_stats = _run_llm_enrichment(
        plugin,
        ai_cfg,
        jobs,
        ctx,
        sink,
        fit_phase,
        run_preflight=run_preflight,
        fit_budget=fit_budget,
        exclude_fit_urls=exclude_fit_urls,
        attempted=attempted,
        force=force,
        cooldown_cutoff=cooldown_cutoff,
    )
    return detail_stats, fn_stats


def _add_stats(into: dict[str, int], more: dict[str, int]) -> None:
    """Accumulate one enrichment stats dict into a running total in place.

    Two-wave runs sum each wave's counters so the end-of-run reconciliation and
    manifest report the combined total, not the last wave alone.
    """
    for key, value in more.items():
        into[key] = into.get(key, 0) + value


def _accumulate_enrich_stats(
    fn_stats: dict[str, int],
    wave_result: dict[str, dict[str, Any]],
) -> None:
    """Fold a completed wave's fit stats into the running total."""
    if "fit" in wave_result:
        _add_stats(fn_stats, wave_result["fit"])


@dataclass
class _RunState:
    """Manifest-relevant state shared between run()'s body and its exit handler.

    A single mutable object so the outer ``except`` can always write a coherent
    interrupted/crashed manifest no matter how far the run got -- avoiding both
    the unbound-local trap (an exception before a bare local is assigned) and a
    250-line re-indent to wrap the live block in a try.
    """

    sink: _JobSink | None = None
    all_jobs: list[dict[str, Any]] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    # Sources that finished but returned INCOMPLETE results (raised
    # PartialSourceError). Distinct from failed_sources -- their rows are kept --
    # but surfaced separately so a partial/all-failed scrape is never read as a
    # clean run. Carried on state so the interrupt-path manifest stays honest.
    degraded_sources: list[str] = field(default_factory=list)
    phase_reached: str = "scraping"
    fn_stats: dict[str, int] = field(default_factory=_empty_fit_stats)
    # The overlap (wave-1) background enrichment thread, exposed so run()'s exit
    # handler can join its in-flight enrichment before the final flush/manifest.
    wave1_thread: threading.Thread | None = None
    # The run's cooperative stop signal, exposed so the interrupt handler can set
    # it before joining wave 1 -- the coordinator's completion loop honors it to
    # cancel pending LLM calls and drain promptly (the bounded join then becomes a
    # real drain rather than a wait for the full remaining budget).
    stop_event: threading.Event | None = None
    # Holds a wave-1 exception relayed from the background thread, so the interrupt
    # handler can surface it (log, not re-raise -- the interrupt exit code wins)
    # rather than letting it vanish when the thread is joined off the normal path.
    wave1_error: list[BaseException] = field(default_factory=list)
    # Wave-1's per-phase stats and whether the normal path already folded them, so
    # the interrupt handler can account wave-1 enrichment in the manifest exactly
    # once (the normal path folds via _accumulate_enrich_stats; the interrupt path
    # folds here after the join, guarded by the flag against double-counting).
    wave1_result: dict[str, Any] = field(default_factory=dict)
    wave1_accumulated: bool = False
    # Backlog-wave counters, carried on state (like fn_stats) so the interrupt
    # manifest reflects backlog progress the teardown flush actually persisted.
    # remaining is set to the full scoreable count before the wave starts and
    # reconciled after it completes.
    backlog_enriched: int = 0
    backlog_remaining: int = 0
    # Alias of ctx.saturation (shared list), so both manifest paths report
    # truncated queries.
    saturation: list[SaturationRecord] = field(default_factory=list)

    @property
    def written(self) -> int:
        return len(self.sink.rows) if self.sink is not None else 0


def _write_run_manifest(
    output_dir: Path,
    *,
    started_at: datetime,
    all_jobs: list[dict[str, Any]],
    failed_sources: list[str],
    degraded_sources: list[str],
    written: int,
    fn_enriched: int,
    phase_reached: str,
    interrupted: bool,
    backlog_enriched: int = 0,
    backlog_remaining: int = 0,
    saturation: list[SaturationRecord] | None = None,
) -> None:
    """Write jobs-last-run.json. Written on BOTH the happy and interrupt paths.

    ``phase_reached`` (scraping|detail|enrichment|complete) and ``interrupted``
    let ``jobs status`` surface a recovery line when a run was cut short, so the
    user knows to finish enrichment with ``jobs backfill``.
    """
    run_manifest = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "sources_ok": sorted(
            {
                j.get("source", "")
                for j in all_jobs
                if j.get("source")
                and j.get("source") not in failed_sources
                and j.get("source") not in degraded_sources
            }
        ),
        "sources_failed": failed_sources,
        "sources_degraded": degraded_sources,
        "new_jobs": written,
        "enriched_fit_notes": fn_enriched,
        "backlog_enriched": backlog_enriched,
        "backlog_remaining": backlog_remaining,
        # Snapshot first: an interrupt-path write can race a still-running
        # phase-1 worker's append.
        "saturated_queries": [asdict(rec) for rec in list(saturation or [])],
        "phase_reached": phase_reached,
        "interrupted": interrupted,
    }
    last_run_path = output_dir / "jobs-last-run.json"
    # Atomic write (temp + fsync + os.replace, mirroring csv_io.atomic_write_rows):
    # `jobs run --json` reads this file back and pipes it downstream, so a partial
    # write must never leave malformed JSON that a consumer then parses. os.replace
    # is atomic on POSIX, so a reader sees either the old file or the complete new
    # one. A mid-write failure unlinks the temp file before propagating to the
    # best-effort handler below.
    tmp_path = last_run_path.with_suffix(last_run_path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(run_manifest, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, last_run_path)
        log.debug("Run manifest written to %s", last_run_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        log.warning("Could not write run manifest: %s", exc)


def run(
    plugin: JobSearchPlugin,
    output_dir: Path,
    ephemeral_dir: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
    dry_run: bool = False,
    no_enrich: bool = False,
    sources_override: list[str] | None = None,
    suppress_live: bool = False,
) -> int:
    """Run all enabled scrapers and append new rows to ``output_dir/jobs.csv``.

    Returns the process-style exit code (0 success, 1 on failed sources /
    persistence degraded / I/O error). Performs no argparse or ``sys.exit()`` --
    the CLI layer is responsible for exit handling.

    ``suppress_live`` forces plain mode (no pinned live block) regardless of TTY;
    the ``jobs run --json`` path sets it so stdout stays a clean JSON channel
    while diagnostics still go to stderr.

    Thin wrapper around :func:`_run_impl` that owns the manifest-on-every-exit
    guarantee: a ``_RunState`` carries the manifest-relevant state, and ANY exit
    that escapes ``_run_impl`` -- a Ctrl-C/SIGTERM during scraping or the
    scrape/enrich overlap window (the longest state, the launchd-SIGTERM case),
    or any crash -- writes an ``interrupted=True`` manifest at the phase reached
    before re-raising. Clean completion writes ``interrupted=False`` inside
    ``_run_impl``. dry-run writes no manifest (it returns before the write).
    """
    started_at = datetime.now(timezone.utc)
    state = _RunState()
    try:
        return _run_impl(
            plugin,
            output_dir,
            ephemeral_dir,
            ai=ai,
            context_text=context_text,
            dry_run=dry_run,
            no_enrich=no_enrich,
            sources_override=sources_override,
            suppress_live=suppress_live,
            started_at=started_at,
            state=state,
        )
    except BaseException:
        # Manifest on every exit. Best-effort guarded flush so a disk error here
        # never masks the original exception (e.g. degrading a SIGINT to exit 1).
        # dry-run has no sink, so there is nothing to persist and no manifest to
        # write (a dry-run leaves the previous run's manifest untouched).
        if not dry_run:
            # Cooperatively stop, then JOIN the overlap (wave-1) background thread
            # before the final flush so its in-flight enrichment lands on disk
            # instead of being abandoned. Setting stop_event makes the wave-1
            # enrichment loop cancel its pending LLM calls and drain promptly, so
            # the BOUNDED join is a real drain in the common case -- not a wait for
            # the full remaining budget. The bound still caps the wait so a second
            # Ctrl-C is never swallowed; if wave 1 does not settle within it, the
            # daemon is left to expire on its own enrich_timeout and we disclose
            # the under-count below. Set unconditionally -- the scrape-phase
            # handler may already have set it, and a redundant set is harmless.
            if state.stop_event is not None:
                state.stop_event.set()
            wave1_thread = state.wave1_thread
            if wave1_thread is not None and wave1_thread.is_alive():
                wave1_thread.join(timeout=_WAVE1_INTERRUPT_JOIN_SECONDS)
                if wave1_thread.is_alive():
                    # The daemon outlived the bound; its remaining results are not
                    # in state.wave1_result, so the manifest will under-report and
                    # the thread may still be flushing rows to jobs.csv. Disclose
                    # rather than fail silently.
                    log.warning(
                        "wave-1 enrichment did not settle within %ss; manifest "
                        "enrichment counts under-report rows still being written "
                        "to %s",
                        _WAVE1_INTERRUPT_JOIN_SECONDS,
                        state.sink.csv_path if state.sink is not None else "jobs.csv",
                    )
            # Surface a relayed wave-1 exception so it does not vanish on this
            # path. Do NOT re-raise -- the interrupt's exit code wins; the error
            # only needs to be visible for diagnosis.
            if state.wave1_error:
                log.error(
                    "wave-1 enrichment raised before the interrupt: %s",
                    state.wave1_error[0],
                )
            # Fold wave-1's enrichment counts into the manifest unless the normal
            # path already did (guards against double-counting).
            if not state.wave1_accumulated and state.wave1_result:
                _accumulate_enrich_stats(state.fn_stats, state.wave1_result)
                state.wave1_accumulated = True
            sink = state.sink
            if sink is not None:
                try:
                    sink.flush()
                except OSError as exc:
                    log.warning(
                        "PERSISTENCE failure on interrupt: could not save %s (%s)",
                        sink.csv_path,
                        exc,
                    )
            _write_run_manifest(
                output_dir,
                started_at=started_at,
                all_jobs=state.all_jobs,
                failed_sources=state.failed_sources,
                degraded_sources=state.degraded_sources,
                written=state.written,
                fn_enriched=state.fn_stats["enriched"],
                backlog_enriched=state.backlog_enriched,
                backlog_remaining=state.backlog_remaining,
                saturation=state.saturation,
                phase_reached=state.phase_reached,
                interrupted=True,
            )
        raise


def _run_impl(
    plugin: JobSearchPlugin,
    output_dir: Path,
    ephemeral_dir: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
    dry_run: bool = False,
    no_enrich: bool = False,
    sources_override: list[str] | None = None,
    suppress_live: bool = False,
    started_at: datetime,
    state: _RunState,
) -> int:
    """The run pipeline. Mutates ``state`` so the :func:`run` wrapper can write a
    coherent manifest on any abrupt exit. See :func:`run`."""
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        load_existing_jobs,
        read_rows,
    )

    csv_path = output_dir / "jobs.csv"
    clear_stale_adjacent_lock(csv_path)
    lock_path = jobs_lock_path(ephemeral_dir)
    ai_cfg = ai or AIConfig()

    if not plugin.scraper.enabled:
        Console.warning(
            "Scraper disabled. Set plugins.job_search.scraper.enabled: true "
            "in .dd-config.yaml"
        )
        return 0

    from daily_driver.plugins.job_search.jobs_archive import load_archive_dedup

    # Announce-then-wait on contention, with a bounded give-up. Returning a clean
    # non-zero here (rather than raising) avoids the run() wrapper writing a
    # misleading interrupted=True manifest for a run that never started.
    lock_stack = ExitStack()
    try:
        lock_stack.enter_context(
            file_lock(
                lock_path,
                timeout=LOCK_WAIT_TIMEOUT_SECONDS,
                on_contention=workspace_busy_notice,
            )
        )
    except TimeoutError:
        Console.error(LOCK_GIVEUP_MESSAGE)
        return 1
    try:
        known_urls, known_keys, header = load_existing_jobs(csv_path)
        # Captured under the same lock as the dedup seed: the sink's flush
        # rewrites the WHOLE file, so it must carry these rows through
        # field-for-field or every pre-existing row would be wiped by the
        # first flush.
        _, preexisting_rows = read_rows(csv_path)
        # A hand-edited row with MORE cells than the header parks the overflow
        # under DictReader's None key, which the rewrite's DictWriter drops
        # (extrasaction="ignore"). The old append-only path never rewrote
        # those bytes; now that every enriching run does, surface the trim
        # instead of letting it happen silently.
        ragged = [r for r in preexisting_rows if None in r]
        if ragged:
            log.warning(
                "jobs.csv has %d row(s) with more cells than the header; the "
                "extra trailing cells will be dropped on the next save: %s",
                len(ragged),
                ", ".join(str(r.get("Link") or "(no link)") for r in ragged),
            )

        # Reconcile the description sidecar against the live jobs.csv URLs
        # BEFORE the archive-dedup merge, so the GC key is the live set only
        # (archived rows never need descriptions -- the store is a pure cache).
        # Real runs only: dry-run writes nothing.
        if not dry_run:
            from daily_driver.plugins.job_search.scraper.descriptions import (
                gc_descriptions,
            )

            dropped = gc_descriptions(csv_path, known_urls)
            if dropped and not suppress_live:
                Console.info(f"Cleaned up {dropped} orphaned description(s).")

        # Union archive-table dedup state so triaged listings (pruned to
        # jobs.archive.csv) are never re-discovered. Verification-closed rows
        # are deliberately absent from the union (re-discoverable); their URLs
        # arrive in reopen_watch so a re-discovery is announced loudly.
        archive_urls, archive_keys, reopen_watch = load_archive_dedup(csv_path)
        known_urls |= archive_urls
        known_keys |= archive_keys

        if not header:
            header = CANONICAL_HEADER
            # dry-run writes nothing at all (in-memory single pass), so skip
            # initializing the file too -- the append path never fires under it.
            if not dry_run:
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(csv_path, "w", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow(header)
                except OSError as exc:
                    log.error("Cannot initialize %s: %s", csv_path, exc)
                    # Overwrite any stale "complete" manifest from a prior run so
                    # `jobs status` reflects this failed run, not the last good one.
                    _write_run_manifest(
                        output_dir,
                        started_at=started_at,
                        all_jobs=[],
                        failed_sources=[],
                        degraded_sources=[],
                        written=0,
                        fn_enriched=0,
                        phase_reached="scraping",
                        interrupted=True,
                    )
                    return 1
    finally:
        lock_stack.close()

    log.info(
        "Loaded %d existing URLs, %d existing keys from %s",
        len(known_urls),
        len(known_keys),
        csv_path,
    )

    # Carry the merged dedup set on the context so adapters that build their
    # URL deterministically (Apple, Wellfound) can short-circuit during
    # pagination instead of waiting for the post-scrape filter below.
    ctx = ScrapeContext(
        plugin=plugin,
        ai=ai_cfg,
        context_text=context_text,
        known_urls=frozenset(known_urls),
    )
    # Expose the cooperative stop signal so run()'s interrupt handler can set it
    # before joining the wave-1 thread (turning the bounded join into a drain).
    state.stop_event = ctx.stop_event
    # Alias the shared saturation list so the interrupt-path manifest reports
    # queries flagged before the interrupt.
    state.saturation = ctx.saturation

    # Coarse run phase for the manifest (scraping -> detail -> enrichment ->
    # complete), held on ``state`` so the run() wrapper's exit handler reads the
    # phase actually reached. Closures (incl. the wave-1 thread) update it.
    def _set_phase(p: str) -> None:
        state.phase_reached = p

    title = "Job search run (dry-run)" if dry_run else "Job search run"
    # The live block renders on any TTY; verbosity controls only how much scrolls
    # above it. enlighten pins the bars in a terminal scroll region and the
    # terminal scrolls log lines above them, so WARN+ (and INFO/DEBUG heartbeats
    # under -v/-vv) stream live as the run progresses while the block stays
    # pinned. Non-TTY (cron/pipe) -- and an unresponsive TTY -- get plain line
    # mode, no block. Quiet mode ("errors only") suppresses the block entirely;
    # the plain-mode progress lines are dropped by RunProgress under quiet too.
    # --json (suppress_live) also forces plain mode so stdout stays clean JSON.
    tty = Console.live_progress_enabled(suppress=suppress_live)
    with (
        live_log_window(tty),
        RunProgress(Console.get_log_console(), tty=tty, title=title) as rp,
    ):
        scrape_group = rp.group("Scraping sources")
        # Rows keyed by source id: created pending when the run order resolves,
        # flipped to running on start, frozen on completion. Tolerant of a
        # start/done without a prior enabled announcement (e.g. in tests).
        source_rows: dict[str, Item] = {}

        def _row(sid: str) -> Item:
            row = source_rows.get(sid)
            if row is None:
                row = scrape_group.item(_display_name(sid))
                source_rows[sid] = row
            return row

        def _on_enabled(sids: list[str]) -> None:
            # Reserve the whole scrape block in one scroll-region set before any
            # row is pinned: title + group header + one bar per source. Avoids
            # the per-bar region resizes that corrupt the display on iTerm2 /
            # VS Code (gap before the block, duplicate block in scrollback).
            rp.reserve(2 + len(sids))
            for sid in sids:
                _row(sid)

        def _on_started(sid: str) -> None:
            _row(sid).start(note=_slow_source_note(sid))

        def _on_progress(sid: str, completed: int, total: int | None) -> None:
            # Fed from each scraper's ctx.report(done, total) callback; upgrades the row to a live bar.
            _row(sid).progress(completed, total)

        def _on_done(sid: str, ok: bool, detail: str) -> None:
            # A checkpoint-aborted source returns normally from its scraper
            # (rows persisted so far), so the orchestrator reports ok=True --
            # but the checkpoint handler already recorded it failed. The live
            # row must agree with the manifest/exit code, not paint green.
            if ok and sid in state.failed_sources:
                ok = False
                detail = f"{detail} (persistence failed)" if detail else "failed"
            _row(sid).finish(ok, detail)

        # The sink IS the durable-record checkpoint: each source's rows are
        # deduped/filtered/lifted and appended to jobs.csv as it lands (the
        # append-as-completed callback below). dry-run keeps the old in-memory
        # single pass with no sink and no writes.
        sink: _JobSink | None = None
        on_source_result: Callable[[str, list[dict[str, Any]]], object] | None = None
        on_source_checkpoint: Callable[[str, list[dict[str, Any]]], object] | None = (
            None
        )
        if not dry_run:
            from daily_driver.plugins.job_search.scraper.descriptions import (
                load_descriptions,
            )

            sink = _JobSink(
                csv_path=csv_path,
                lock_path=lock_path,
                header=header,
                known_urls=known_urls,
                known_keys=known_keys,
                plugin=plugin,
                preexisting_rows=preexisting_rows,
                descriptions=load_descriptions(csv_path),
            )
            sink.reopen_watch = reopen_watch
            state.sink = sink

            def _on_source_result(source_id: str, jobs: list[dict[str, Any]]) -> None:
                # Per-source isolation: an append OSError (raised as ScraperError
                # by the sink) must mark just this source failed and let the
                # remaining sources finish -- not kill the whole run with later
                # sources lost. The sink already rolled back its state on failure
                # (no phantom dedup/rows), so the source is cleanly absent.
                try:
                    sink.append_source(source_id, jobs)
                except ScraperError as exc:
                    log.error(
                        "[%s] could not persist scraped rows, marking source "
                        "failed: %s",
                        source_id,
                        exc,
                    )
                    if source_id not in state.failed_sources:
                        state.failed_sources.append(source_id)

            on_source_result = _on_source_result

            def _on_source_checkpoint(
                source_id: str, jobs: list[dict[str, Any]]
            ) -> None:
                # Per-unit checkpoint path. On a persist failure mark the source
                # failed AND raise CheckpointAborted so the source stops at this
                # unit -- otherwise it would scrape on against a dead disk and a
                # later unit could land rows for a source already reported failed.
                # "failed" then means "stopped at the failure".
                try:
                    sink.append_source(source_id, jobs)
                except ScraperError as exc:
                    log.error(
                        "[%s] could not persist a scraped unit, stopping the "
                        "source and marking it failed: %s",
                        source_id,
                        exc,
                    )
                    if source_id not in state.failed_sources:
                        state.failed_sources.append(source_id)
                    raise CheckpointAborted(str(exc)) from exc

            on_source_checkpoint = _on_source_checkpoint

        # Overlap state (Stage 3). When a phase 2 (Apple) follows phase 1, the
        # phase-1 rows are enriched in a background WAVE while Apple still
        # scrapes; Apple's rows get a second wave afterward. Budgets are SHARED
        # running totals across waves. Enrichment runs on the wave-1 thread here,
        # not the main thread, so the per-enricher SIGINT notifier no-ops (main-
        # thread only); a child thread never receives the KeyboardInterrupt. On an
        # interrupt during the overlap, run()'s handler sets ctx.stop_event and
        # bounded-joins this thread: the coordinator's completion loop honors the
        # event to cancel pending LLM calls and drain what settled, so the join is
        # a cooperative drain in the common case rather than a wait for the full
        # remaining budget. If wave 1 still outlives the bound (a stuck in-flight
        # call), the handler logs a disclosure that the manifest under-reports and
        # the daemon may still be flushing -- it is NOT a hard guarantee that every
        # wave-1 result lands, but it is no longer silently abandoned.
        enrich_cfg = plugin.enrichment
        do_enrich = not (dry_run or no_enrich)

        enrich_group: Group | None = None
        wave1_thread: threading.Thread | None = None
        # Holds wave-1's per-phase stats plus the attempted-identity set
        # (fit_attempted_urls) so wave 2 can exclude those rows and size the
        # shared budget by what wave 1 actually attempted. Aliased to state so
        # the run() interrupt handler can fold wave-1 stats into the manifest and
        # surface a relayed wave-1 exception after the join.
        wave1_result: dict[str, Any] = state.wave1_result
        wave1_error: list[BaseException] = state.wave1_error
        wave1_count = [0]  # phase-1 rows handed to wave 1

        def _run_wave1(phase1_jobs: list[EnrichedJob]) -> None:
            assert sink is not None and enrich_group is not None
            try:
                attempted: dict[str, set[str]] = {}
                d, f = _enrich_wave(
                    plugin,
                    ai_cfg,
                    phase1_jobs,
                    ctx,
                    sink,
                    enrich_group,
                    wave_label="",
                    run_preflight=True,
                    fit_budget=None,  # wave 1 gets the full config budget
                    set_phase=_set_phase,
                    attempted=attempted,
                )
                wave1_result.update(
                    {
                        "detail": d,
                        "fit": f,
                        "fit_attempted_urls": attempted.get("fit_urls", set()),
                    }
                )
            except BaseException as exc:  # noqa: BLE001 -- relayed to main thread
                wave1_error.append(exc)

        def _on_phase1_done(has_phase2: bool) -> None:
            # Only overlap when an Apple phase actually follows -- otherwise the
            # single post-scrape wave on the main thread is simpler and keeps the
            # interrupt notifier live.
            if not (do_enrich and has_phase2) or sink is None:
                return
            nonlocal enrich_group, wave1_thread
            # Hand the wave the REAL sink.rows, not a copy: the enrichers
            # propagate results by replacing list slots (frozen EnrichedJob), and
            # only the live sink.rows is what flush() reads. Wave 1 iterates this
            # LIVE list, so once Apple's concurrent .extend() appends phase-2 rows
            # it may also touch indices >= wave1_count (any rows present when its
            # plan/stitch runs). That is harmless: slot replacement on a Python
            # list is GIL-atomic so it never races Apple's append, and wave 2
            # dedups by the attempted-identity sets (fit URLs / companies), not by
            # index, so a phase-2 row wave 1 happened to enrich is simply excluded
            # from wave 2 rather than re-charged. wave1_count is pinned here only
            # to size wave 2's "is there leftover work" check, not to bound which
            # slots wave 1 writes. The lock is held only to read len consistently.
            with sink._rows_lock:
                phase1_jobs = sink.rows
                wave1_count[0] = len(phase1_jobs)
            enrich_group = rp.group("Enriching jobs")
            wave1_thread = threading.Thread(
                target=_run_wave1, args=(phase1_jobs,), daemon=True
            )
            state.wave1_thread = wave1_thread
            wave1_thread.start()

        def _on_degraded(source_id: str, reason: str) -> None:
            # A source finished but its scrape is incomplete (PartialSourceError);
            # record it as degraded -- distinct from failed -- so the summary and
            # manifest stay honest. Its partial rows are kept (appended normally).
            if source_id not in state.degraded_sources:
                state.degraded_sources.append(source_id)

        all_jobs, scrape_failed, source_results = run_all_scrapers(
            ctx,
            sources_override=sources_override,
            on_sources_enabled=_on_enabled,
            on_source_start=_on_started,
            on_source_progress=_on_progress,
            on_source_done=_on_done,
            on_note=rp.note,
            on_source_result=on_source_result,
            on_source_degraded=_on_degraded,
            # Slow jobspy sources checkpoint each finished (term x country) unit
            # through the failure-isolated append path, so a crash/kill keeps every
            # completed unit (not the whole-source-or-nothing of before). On a
            # persist failure the checkpoint path stops the source (raises
            # CheckpointAborted) rather than scraping on against a dead disk.
            on_source_checkpoint=on_source_checkpoint,
            on_phase1_done=_on_phase1_done,
            # Force Apple headless while the live block owns the terminal.
            force_headless=tty,
        )
        state.all_jobs = all_jobs
        # Merge scraper-level failures with any append-persistence failures the
        # source-result callback recorded; preserve order, no duplicates.
        for sid in scrape_failed:
            if sid not in state.failed_sources:
                state.failed_sources.append(sid)
        failed_sources = state.failed_sources
        degraded_sources = state.degraded_sources

        if failed_sources:
            log.warning("Failed sources: %s", ", ".join(failed_sources))
        if degraded_sources:
            log.warning(
                "Degraded sources (incomplete results, kept what was scraped): %s",
                ", ".join(degraded_sources),
            )

        if sink is not None:
            # Append-time funnel: the sink classified every row as it was written,
            # so the totals and per-source breakdown are already accumulated.
            raw_found = sink.raw_found
            pre_filter = sink.pre_filter
            filtered = sink.loc_filtered
            funnel = sink.funnel
            typed_jobs = sink.rows
        else:
            # dry-run: replicate the former in-memory dedup/filter/lift in one
            # pass so the preview table and Completed line match a real run.
            new_jobs = [
                j
                for j in all_jobs
                if (not j.get("url") or j["url"] not in known_urls)
                and dedup_key(j.get("company", ""), j.get("role", "")) not in known_keys
            ]
            urlless = [j for j in new_jobs if not j.get("url")]
            if urlless:
                log.warning(
                    "Dropping %d jobs with no URL (cannot dedup on future runs)",
                    len(urlless),
                )
            new_jobs = [j for j in new_jobs if j.get("url")]
            log.info("Found %d jobs total, %d new", len(all_jobs), len(new_jobs))
            pre_filter = len(new_jobs)
            new_jobs = [j for j in new_jobs if location_matches(j, plugin)]
            filtered = pre_filter - len(new_jobs)
            if filtered:
                log.info("Filtered %d jobs by location preferences", filtered)
            raw_found = sum(
                len(jobs)
                for _, jobs in source_results
                if not isinstance(jobs, Exception)
            )
            funnel = _per_source_funnel(source_results, known_urls, known_keys, plugin)
            typed_jobs = [_enriched_from_scraped(j) for j in new_jobs]

        scrape_group.done(f"{raw_found} found, {pre_filter} new")

        # Colour each finished source bar by what it found. The funnel persists
        # past the `with` block (it is not a scope), so it is reused for the
        # summary after teardown.
        for sid, counts in funnel.items():
            row = source_rows.get(sid)
            if row is not None:
                row.show_breakdown(_source_breakdown_segments(counts))

        # Accumulate enrichment counters straight into state so the run()
        # wrapper's manifest reflects partial progress on an interrupt.
        fn_stats = state.fn_stats
        # Backlog (never-enriched pre-existing rows) counters. Disjoint from
        # fn_stats/written by design: those must stay new-rows-only.
        folded_candidates: list[EnrichedJob] = []
        folded_stats = _empty_fit_stats()
        folded_leftover_budget = 0
        backlog_scoreable_total = 0
        backlog_no_desc = 0
        if not do_enrich:
            # dry-run avoids writes entirely; --no-enrich appends the lifted rows
            # unenriched for a later backfill. Render no enrichment bars and
            # report zero counters.
            log.info(
                "[%s] skipping enrichment (no detail/LLM calls)",
                "dry-run" if dry_run else "no-enrich",
            )
        else:
            # The sink is the durable checkpoint: each wave mutates its rows in
            # place and flushes jobs.csv per completed phase and every ~25 LLM
            # results. An interrupt/crash is caught by the run() wrapper, which
            # flushes the partial pass and writes the interrupted manifest; a
            # mid-enrichment loss is at most one flush window (jobs backfill resumes).
            assert sink is not None  # not dry_run -> sink exists
            # Wave 1 (phase-1 rows) may already be running in a background thread
            # (started at on_phase1_done). Join it, then enrich the remaining
            # (Apple) rows as wave 2 with the budget wave 1 left.
            # Cumulative fit attempts across the new-row waves: sizes the
            # backlog wave's leftover budget, so missing a wave here would
            # over-grant it.
            fit_attempted_all: set[str] = set()
            if wave1_thread is not None:
                wave1_thread.join()
                if wave1_error:
                    raise wave1_error[0]
                assert enrich_group is not None
                _accumulate_enrich_stats(fn_stats, wave1_result)
                state.wave1_accumulated = True
                fit_attempted_all |= wave1_result.get("fit_attempted_urls", set())
                # Wave 2 receives the WHOLE row list (so its slot mutations and
                # the periodic flush both target sink.rows directly). Rows ALREADY
                # ATTEMPTED in wave 1 -- enriched or failed -- are excluded so a
                # failed wave-1 row is not retried (and double-charged) in wave 2;
                # backfill is the retry path. The fit budget is a shared running
                # total: wave 2 gets what wave 1 left, counted by jobs attempted.
                if len(typed_jobs) > wave1_count[0]:
                    fit_attempted = frozenset(
                        wave1_result.get("fit_attempted_urls", set())
                    )
                    wave2_attempted: dict[str, set[str]] = {}
                    _, f2 = _enrich_wave(
                        plugin,
                        ai_cfg,
                        typed_jobs,
                        ctx,
                        sink,
                        enrich_group,
                        wave_label=" (wave 2)",
                        run_preflight=False,  # wave 1 already probed
                        # Shared running total: wave 2 gets exactly what wave 1
                        # left -- 0 when exhausted (an explicit 0 makes no calls;
                        # the wave still runs its unbudgeted detail enrichment).
                        fit_budget=max(
                            0, enrich_cfg.max_enrich_fit - len(fit_attempted)
                        ),
                        set_phase=_set_phase,
                        exclude_fit_urls=fit_attempted,
                        attempted=wave2_attempted,
                    )
                    _add_stats(fn_stats, f2)
                    fit_attempted_all |= wave2_attempted.get("fit_urls", set())
            else:
                # No overlap (no Apple phase, or it landed nothing): one wave
                # over every row on the main thread, same as the classic path.
                enrich_group = rp.group("Enriching jobs")
                single_attempted: dict[str, set[str]] = {}
                _, f1 = _enrich_wave(
                    plugin,
                    ai_cfg,
                    typed_jobs,
                    ctx,
                    sink,
                    enrich_group,
                    wave_label="",
                    run_preflight=True,
                    fit_budget=None,
                    set_phase=_set_phase,
                    attempted=single_attempted,
                )
                _add_stats(fn_stats, f1)
                fit_attempted_all |= single_attempted.get("fit_urls", set())
            if enrich_group is not None:
                enrich_group.done()

            # Backlog wave: never-enriched pre-existing rows, lifted from the
            # run-start snapshot and hydrated read-only from the sidecar.
            # Enriched with ONLY the fit budget the new-row waves left,
            # cache-only descriptions (backfill parity). Kept off sink.rows so
            # jobs.csv order and new_jobs stay new-rows-only; landed in place
            # at flush via _apply_folded_updates.
            from daily_driver.plugins.job_search.scraper.models import (
                ENRICH_ELIGIBLE_STATUSES,
                EnrichedJob,
            )

            backlog_scoreable: list[EnrichedJob] = []
            for raw_row in sink.preexisting_rows:
                # Raw-cell gate first: the enriched majority of a mature
                # jobs.csv never pays a model lift.
                if (raw_row.get("Date Enriched") or "").strip():
                    continue
                job = EnrichedJob.from_csv_row(raw_row)
                if job.status not in ENRICH_ELIGIBLE_STATUSES:
                    continue
                if job.fit and job.notes:
                    # Scored before the Date Enriched column existed; only
                    # backfill --force-update re-scores. Counting it here
                    # would inflate "remaining" forever.
                    continue
                if not job.description_text:
                    cached = sink._descriptions.get(job.url, "")
                    if cached.strip():
                        job = job.with_updates(description_text=cached)
                if not job.description_text.strip():
                    # Unscorable until a re-scrape heals the description;
                    # entering the wave would re-fetch its detail page every
                    # run for nothing.
                    backlog_no_desc += 1
                    continue
                backlog_scoreable.append(job)
            folded_leftover_budget = max(
                0, enrich_cfg.max_enrich_fit - len(fit_attempted_all)
            )
            backlog_scoreable_total = len(backlog_scoreable)
            # Cap candidates to the leftover budget: the wave's detail phase
            # is otherwise unbudgeted, so this bounds its fetches to rows the
            # fit pass can actually score this run. Zero leftover (or zero
            # scoreable) skips the wave entirely.
            folded_candidates = backlog_scoreable[:folded_leftover_budget]
            state.backlog_remaining = backlog_scoreable_total
            if folded_candidates:
                # The SAME list object the wave mutates via slot replacement --
                # flush_periodic reads sink.folded_rows, so a copy would lose
                # mid-run progress (cf. backfill's sink.rows = jobs).
                sink.folded_rows = folded_candidates
                folded_group = rp.group("Enriching backlog")
                _, folded_stats = _enrich_wave(
                    plugin,
                    ai_cfg,
                    folded_candidates,
                    ctx,
                    sink,
                    folded_group,
                    wave_label=" (backlog)",
                    run_preflight=False,
                    fit_budget=folded_leftover_budget,
                    set_phase=_set_phase,
                    exclude_fit_urls=frozenset(fit_attempted_all),
                    capture_descriptions=False,
                )
                folded_group.done()
                state.backlog_enriched = folded_stats["enriched"]
                state.backlog_remaining = (
                    backlog_scoreable_total - folded_stats["enriched"]
                )

    n = len(typed_jobs)
    log.info(
        "Fit+Notes enriched: %d/%d, %d skipped (budget), %d no description, "
        "%d failed (parse/subprocess)",
        fn_stats["enriched"],
        n,
        fn_stats["skipped_budget"],
        fn_stats.get("no_description", 0),
        fn_stats["failed"],
    )

    # ── Scraping funnel ──────────────────────────────────────────────────────
    # Headline that reconciles every job: found (raw scraped) -> new (not already
    # in csv) -> matched location (the only filter that removes jobs). Intra/
    # cross-run duplicates are the remainder. `funnel` (the per-source breakdown)
    # was computed above while the live bars were up; it is reused here. The
    # re-sighting split is a scrape fact, so it lives here (not under Enrichment)
    # and reports even under --no-enrich.
    still_visible, not_seen, healed = (
        sink.rescan_summary() if sink is not None else (0, 0, 0)
    )
    label_width = max([len(_display_name(sid)) for sid in funnel] + [len("Re-seen")])
    Console.info("Scraping")
    summary = (
        f"  Completed: {raw_found} found -> {pre_filter} new "
        f"-> {len(typed_jobs)} matched location"
    )
    if filtered:
        summary += f" ({filtered} skipped by location)"
    Console.info(summary)
    for sid, c in funnel.items():
        # The grey "other" bar segment (within-run duplicates + url-less rows)
        # has no count of its own in the funnel; surface it here only when
        # present so no datum lives in colour alone.
        other = _funnel_other(c)
        line = (
            f"  {_display_name(sid):<{label_width}}  {c['found']} found, "
            f"{c['new']} new, {c['known']} already in csv, "
            f"{c['loc_skip']} skipped (location)"
        )
        if other > 0:
            line += f", {other} other"
        Console.info(line)
    # Re-sighting freshness split: pre-existing rows the scrape re-confirmed live
    # vs. did not return. "not seen this run" is deliberately not "gone" -- a live
    # job from an unscraped source/window looks identical.
    if still_visible or not_seen:
        reseen_line = (
            f"  {'Re-seen':<{label_width}}  {still_visible} still visible, "
            f"{not_seen} not seen this run"
        )
        if healed:
            reseen_line += f"; {healed} descriptions healed"
        Console.info(reseen_line)
    # Reopened: a job that verification once closed came back in a scrape.
    # Loud -- a genuinely re-posted role or a false-positive closure healing.
    if sink is not None and sink.reopened:
        Console.warning(
            f"  {'Reopened':<{label_width}}  {sink.reopened} previously-closed "
            "job(s) re-discovered (see log for identities)"
        )
    # Degraded sources finished with INCOMPLETE results (a partial-pagination or
    # all-units-failed scrape). Their rows are kept, but call it out so a partial
    # run is never mistaken for a clean one. Distinct from failed sources below.
    if degraded_sources:
        Console.warning(
            "  Degraded sources (incomplete results, kept what was scraped): "
            + ", ".join(_display_name(sid) for sid in degraded_sources)
        )
    # Truncated queries: a query that returned its full cap saw only part of
    # its window. One line per (source, kind) so a mixed LinkedIn run shows
    # BOTH remediations (capped terms are fixable for free; the plateau is
    # not); full per-query detail is in the manifest (saturated_queries) and
    # the -v log. Hints are per-source: each has its own limit knob.
    if ctx.saturation:
        by_group: dict[tuple[str, str], list[SaturationRecord]] = {}
        for rec in ctx.saturation:
            by_group.setdefault((rec.source, rec.kind), []).append(rec)
        for (sid, kind), recs in by_group.items():
            shown = ", ".join(rec.query for rec in recs[:3])
            more = f" (+{len(recs) - 3} more)" if len(recs) > 3 else ""
            hint = _SATURATION_HINTS.get(
                (sid, kind), "results truncated; coverage incomplete"
            )
            Console.warning(
                f"  Saturated  {_display_name(sid)}: {shown}{more} -- {hint}"
            )

    # ── Enrichment funnel ────────────────────────────────────────────────────
    # Only when enrichment actually ran (a dry-run or --no-enrich pass scores
    # nothing, so a zeroed section would be noise).
    no_description_count = fn_stats.get("no_description", 0)
    if do_enrich:
        Console.info("Enrichment")
        fit_line = (
            f"  {'Fit/Notes':<{label_width}}  {fn_stats['enriched']} scored, "
            f"{fn_stats['skipped_budget']} over budget, "
            f"{no_description_count} no description"
        )
        if fn_stats["failed"]:
            fit_line += f", {fn_stats['failed']} failed"
        Console.info(fit_line)
        # Backlog: pre-existing never-enriched rows folded into this run.
        # Remaining rows stay eligible -- the next run (or jobs backfill)
        # picks them up; awaiting-description rows need a re-scrape to heal
        # their description first.
        if backlog_scoreable_total or backlog_no_desc:
            backlog_line = (
                f"  {'Backlog':<{label_width}}  "
                f"{folded_stats['enriched']} scored, "
                f"{state.backlog_remaining} remaining"
            )
            detail_bits = []
            if backlog_no_desc:
                detail_bits.append(f"{backlog_no_desc} awaiting description")
            if folded_stats["failed"]:
                detail_bits.append(f"{folded_stats['failed']} failed")
            if state.backlog_remaining and folded_leftover_budget == 0:
                detail_bits.append("no fit budget left this run")
            if detail_bits:
                backlog_line += " (" + ", ".join(detail_bits) + ")"
            Console.info(backlog_line)
        # Rows with no obtainable description (e.g. a signup-walled posting) are
        # intentionally left un-scored rather than guessed at; call it out so the
        # user knows to re-scrape rather than assume the row was simply skipped.
        if no_description_count:
            Console.warning(
                f"  {no_description_count} job(s) had no description; Fit/Notes "
                "left blank. Delete the row and re-run a scrape to retry."
            )

    if dry_run:
        Console.success(
            f"Dry-run complete: {len(typed_jobs)} new jobs ready (nothing written)."
        )
        _print_dry_run_table(typed_jobs)
        return 1 if failed_sources else 0

    # Rows were already appended per-source during scraping (the durable-record
    # checkpoint). --no-enrich leaves the rows as appended (no rewrite needed).
    assert sink is not None  # dry_run returned above; non-dry-run always has a sink
    written = len(sink.rows)
    skip_note = " (enrichment skipped)" if no_enrich else ""
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    Console.success(
        f"Scraper complete: {written} new jobs appended to {csv_path}.{skip_note} "
        f"Total run time: {_fmt_duration(elapsed)}."
    )

    # Write the completion manifest BEFORE the final flush, so a final-flush
    # failure (raised loudly below) still leaves an honest manifest behind.
    state.phase_reached = "complete"
    _write_run_manifest(
        output_dir,
        started_at=started_at,
        all_jobs=all_jobs,
        failed_sources=failed_sources,
        degraded_sources=state.degraded_sources,
        written=written,
        fn_enriched=fn_stats["enriched"],
        backlog_enriched=state.backlog_enriched,
        backlog_remaining=state.backlog_remaining,
        saturation=ctx.saturation,
        phase_reached=state.phase_reached,
        interrupted=False,
    )

    if not no_enrich:
        # The final rewrite through the backfill rewrite path persists the
        # in-place enrichment; the lock is taken only for the rewrite, never
        # across LLM calls, so a concurrent prune/backfill is serialized but not
        # starved. If periodic saves degraded mid-run, say so once; then let any
        # final-flush OSError propagate loudly -- a silent disk failure must not
        # look like a healthy run when hours of enrichment live only in memory.
        if sink.persistence_degraded:
            Console.warning(
                f"periodic saves failed during the run "
                f"({sink._degraded_reason}); retrying the final save now"
            )
        sink.flush()
    elif sink.has_resightings() or sink._descriptions_dirty:
        # --no-enrich makes no enrichment rewrite, but scrape facts still need
        # persisting: a re-sighting's refreshed Date Verified / healed
        # description, and any description captured at scrape (the sidecar is
        # only written by flush -- skipping it here would silently drop every
        # scraped description, leaving the backlog wave and backfill nothing
        # to score). This is the one rewrite a --no-enrich run makes.
        try:
            sink.flush()
        except OSError as exc:
            log.warning(
                "PERSISTENCE failure: could not save re-sightings to %s (%s)",
                sink.csv_path,
                exc,
            )

    if failed_sources:
        log.error("Scraper failures: %s", ", ".join(failed_sources))
        return 1

    # A run whose periodic saves degraded -- even when the final flush recovered
    # the data on disk -- exits non-zero so a scripted/scheduled caller treats
    # the run as not-fully-clean and can re-run or alert. The data is persisted;
    # the exit code reports that the run did not proceed normally throughout.
    if not no_enrich and sink.persistence_degraded:
        return 1

    return 0
