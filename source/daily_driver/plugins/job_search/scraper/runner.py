"""Scraper orchestration: ScrapeContext, dedup logic, run() / run_backfill()."""

from __future__ import annotations

import csv
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from daily_driver.core.config_models import AIConfig
from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import (
    adopt_third_party_loggers,
    get_logger,
    live_log_window,
)
from daily_driver.core.progress import Group, Item, Phase, RunProgress
from daily_driver.integrations.notify import desktop_notify
from daily_driver.plugins.job_search.config import (
    IndeedToggle,
    JobSearchPlugin,
    LinkedInToggle,
    SourceToggle,
)
from daily_driver.plugins.job_search.jobs_lock import (
    clear_stale_adjacent_lock,
    jobs_lock_path,
)
from daily_driver.plugins.job_search.scraper.countries import country_names
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


class ScraperError(RuntimeError):
    """Raised on unrecoverable scraper errors.

    Library code raises this instead of calling sys.exit() so the CLI layer
    can decide how to report and exit.
    """


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
            "role": job.get("role", "") or "(unknown)",
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

# Site-named sources backed by python-jobspy. When both run with equal query
# knobs the runner merges them into one backend call (see _jobspy_scrape_plan);
# the library is an implementation detail, never part of the user surface.
_JOBSPY_SITES: tuple[str, ...] = ("linkedin", "indeed")

# Display names for the live scraping rows. Source ids are pipeline-internal;
# these are what a user reads. Unmapped ids fall back to a de-underscored form.
# A merged jobspy run uses its own joined row id ("linkedin + indeed"), which
# already reads correctly without a mapping entry.
_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "weworkremotely": "we work remotely",
    "hn_who_is_hiring": "hn who's hiring",
    "hn_jobs": "hn jobs",
}


def _jobspy_toggle(plugin: JobSearchPlugin, site: str) -> LinkedInToggle | IndeedToggle:
    """The typed toggle for a jobspy-backed site (linkedin / indeed)."""
    if site == "linkedin":
        return source_toggle(plugin, "linkedin", LinkedInToggle)
    return source_toggle(plugin, "indeed", IndeedToggle)


def _jobspy_scrape_plan(
    jobspy_sites: list[str], plugin: JobSearchPlugin
) -> list[tuple[str, list[str]]]:
    """Decide how the enabled jobspy-backed sites are fetched.

    Returns ``(row_id, sites)`` entries. When both LinkedIn and Indeed are
    enabled AND their shared query knobs (``results_wanted_per_query`` /
    ``hours_old``) are equal, they collapse to one merged backend call under the
    joined row id ``"linkedin + indeed"``. Differing knobs (or only one site
    enabled) fall back to one call per site, each under its own site-named row.
    This is the deliberately simple rule: equal knobs merge, anything else
    splits. ``jobspy_sites`` preserves registry order so the merged list reads
    ``["linkedin", "indeed"]``.
    """
    sites = [s for s in _JOBSPY_SITES if s in jobspy_sites]
    if len(sites) < 2:
        return [(s, [s]) for s in sites]

    linkedin = _jobspy_toggle(plugin, "linkedin")
    indeed = _jobspy_toggle(plugin, "indeed")
    knobs_match = (
        linkedin.results_wanted_per_query == indeed.results_wanted_per_query
        and linkedin.hours_old == indeed.hours_old
    )
    if knobs_match:
        return [(" + ".join(sites), list(sites))]
    return [(s, [s]) for s in sites]


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

    The jobspy-backed rows are slow regardless of merge vs split; match any row
    id covering linkedin / indeed (single site or the joined "linkedin + indeed"
    label) rather than enumerating every combination.
    """
    if any(site in source_id for site in _JOBSPY_SITES):
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
    a merged/split scrape bound to a row id that may not be a registry key.
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
    start = time.perf_counter()
    try:
        jobs = scraper_fn(ctx)
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
        on_source_done(
            source_id, True, f"{len(jobs)} found in {_fmt_duration(elapsed)}"
        )
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
    file from ``rows`` through the one backfill rewrite path so a mid-enrichment
    crash loses at most one flush window. All sink methods run on the
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
    ) -> None:
        self.csv_path = csv_path
        self.lock_path = lock_path
        self.header = header
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
        # Master list every enricher mutates in place; also the flush source.
        self.rows: list[EnrichedJob] = []
        # Guards growth of ``rows`` vs. a flush snapshot: during scrape/enrich
        # overlap the Apple wave's append_source extends the list on the
        # coordinator thread while the wave-1 enrichment thread flushes it.
        # Slot replacement (out[i] = ...) is GIL-atomic and only ever touches an
        # index the wave already owns, so it needs no lock; only extend/snapshot
        # race, and this serializes them.
        self._rows_lock = threading.Lock()
        # Per-source funnel accumulated at append time (mirrors _per_source_funnel).
        self.funnel: dict[str, dict[str, int]] = {}
        # Run totals for the reconciling Completed line.
        self.raw_found = 0
        self.pre_filter = 0  # new (deduped, url-bearing, pre-location) survivors
        self.loc_filtered = 0

    def append_source(
        self, source_id: str, jobs: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Dedup/filter/lift one source's rows, append them, return its funnel.

        Returns the per-source counts (found/new/known/loc_skip). Updates the
        run's dedup state and totals so cross-source duplicates are caught and
        the Completed line reconciles across sources.
        """
        from daily_driver.plugins.job_search.scraper.csv_io import append_jobs_typed

        counts = self.funnel.setdefault(
            source_id, {"found": 0, "new": 0, "known": 0, "loc_skip": 0}
        )
        # Intra-source dedup: a single source's list may repeat a row (e.g.
        # Apple's locale loop). Those are the funnel's "other" -- found but not
        # classified -- so detect them here without counting them as "known".
        seen_urls: set[str] = set()
        seen_keys: set[str] = set()
        to_append: list[EnrichedJob] = []
        for job in jobs:
            counts["found"] += 1
            self.raw_found += 1
            url = job.get("url", "")
            key = dedup_key(job.get("company", ""), job.get("role", ""))
            if (url and url in seen_urls) or (key and key in seen_keys):
                continue  # within-source duplicate -> "other"
            if url:
                seen_urls.add(url)
            if key:
                seen_keys.add(key)
            if (url and url in self._known_urls) or (key and key in self._known_keys):
                counts["known"] += 1
                continue
            if not url:
                # url-less new jobs cannot be deduped on future runs; drop them.
                log.warning(
                    "Dropping job with no URL (cannot dedup on future runs): %s",
                    job.get("company", ""),
                )
                continue
            if not location_matches(job, self._plugin):
                counts["loc_skip"] += 1
                self.loc_filtered += 1
                continue
            counts["new"] += 1
            self.pre_filter += 1
            # Grow the known set immediately so the next source (and the Apple
            # wave) dedups against this just-accepted row.
            self._known_urls.add(url)
            if key:
                self._known_keys.add(key)
            to_append.append(_enriched_from_scraped(job))

        if to_append:
            with self._rows_lock:
                self.rows.extend(to_append)
            with file_lock(self.lock_path):
                append_jobs_typed(self.csv_path, to_append, self.header)
        return counts

    def flush(self) -> None:
        """Rewrite jobs.csv from ``rows`` through the one backfill rewrite path.

        Holds the sentinel lock only around the rewrite, never across the slow
        LLM calls that produced the updates — matching run()'s append discipline
        so a concurrent prune/backfill is serialized but not starved.
        """
        from daily_driver.plugins.job_search.scraper.csv_io import (
            atomic_write_rows,
            read_rows,
        )

        # Snapshot the row list under the rows lock so a concurrent append (the
        # overlapping Apple wave) cannot mutate it mid-iteration. The slow csv
        # write then runs against the immutable snapshot.
        with self._rows_lock:
            snapshot = list(self.rows)
        with file_lock(self.lock_path):
            # Carry through any unknown/hand-added columns verbatim, indexed by
            # Link, so a flush never drops a user's "Priority" column. The append
            # path only ever wrote canonical columns, so extras come from rows
            # that pre-dated this run.
            stored_header, stored_rows = read_rows(self.csv_path)
            extra_columns = [c for c in stored_header if c not in self.header]
            extras_by_link: dict[str, dict[str, str]] = {}
            if extra_columns:
                for row in stored_rows:
                    link = (row.get("Link") or "").strip()
                    if link:
                        extras_by_link[link] = {
                            c: row.get(c, "") for c in extra_columns
                        }
            out_header = self.header + extra_columns
            out_rows: list[dict[str, str]] = []
            for job in snapshot:
                csv_row = job.to_csv_row()
                if extra_columns:
                    csv_row.update(extras_by_link.get(job.url, {}))
                out_rows.append(csv_row)
            atomic_write_rows(self.csv_path, out_header, out_rows)


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

    # Collapse the enabled jobspy-backed sites into scrape-plan rows: one merged
    # row when both run with equal knobs, else one row per site. Each row is a
    # phase-1 work item bound to a scrape over its site list.
    jobspy_plan = _jobspy_scrape_plan(jobspy_enabled, ctx.plugin)

    # Phase-1 work items: (row_id, scraper_fn). A None scraper_fn means look the
    # row id up in SCRAPERS; the jobspy rows carry an explicit bound scrape so
    # the merged row id (e.g. "linkedin + indeed") needs no registry entry.
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
                ): row_id
                for row_id, fn in headless_items
            }
            for fut in as_completed(futures):
                sid = futures[fut]
                result = fut.result()
                results.append((sid, result))
                # Append-as-completed: hand each successful source straight to
                # the sink on this (coordinator) thread so its rows reach
                # jobs.csv before the next source finishes. A crash mid-phase
                # then loses only the in-flight source.
                if on_source_result is not None and not isinstance(result, Exception):
                    on_source_result(sid, result)
        except KeyboardInterrupt:
            # Drop pending (unstarted) futures and stop waiting on the pool.
            # In-flight HTTP requests cannot be killed mid-call; they run to
            # their per-source ``timeout`` before their threads exit. The CLI
            # boundary catches this re-raise and prints a clean message.
            pool.shutdown(wait=False, cancel_futures=True)
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
    # default to dodge bot detection; forced headless via force_headless.
    if visible_sources:
        visible_ctx = _ctx_with_headless(ctx, force_headless)
        log.info(
            "[phase2] running %d %s scrapers serially (%s)",
            len(visible_sources),
            "headless" if force_headless else "non-headless",
            ", ".join(visible_sources),
        )
        for sid in visible_sources:
            result = _run_one(
                sid,
                visible_ctx,
                on_source_done,
                on_source_start,
                on_source_progress,
            )
            results.append((sid, result))
            if on_source_result is not None and not isinstance(result, Exception):
                on_source_result(sid, result)

    all_jobs, failed_sources = _merge_and_dedup(results)
    return all_jobs, failed_sources, results


# ── Ollama enrichment preflight ──────────────────────────────────────────────


# Independent of the configured per-call enrich_timeout: this single reachability
# probe should fail fast on a down server, not wait out a tuning value meant for
# real generation calls.
_OLLAMA_PREFLIGHT_TIMEOUT = 3


def _llm_enrichment_requested(plugin: JobSearchPlugin) -> bool:
    """Whether this run will make ollama LLM calls (detail pages don't count).

    Detail-page enrichment is plain HTTP against the job board, so it needs no
    provider. Only the product / gd-rating / fit / notes passes route to the
    LLM, so the preflight is warranted only when at least one of those is on.
    """
    cfg = plugin.enrichment
    return (
        cfg.enrich_product or cfg.enrich_gd_rating or cfg.enrich_fit or cfg.enrich_notes
    )


def _ollama_enrichment_preflight(plugin: JobSearchPlugin, ai: AIConfig) -> bool:
    """Ping ollama once before LLM enrichment; return False (and warn) to skip.

    Mirrors the claude which-guard intent — verify the provider is usable before
    the per-job loop — but for ollama the check is a network probe, so it runs
    here at run start rather than per enricher. One cheap ``list_models`` GET on
    a short fixed timeout (:data:`_OLLAMA_PREFLIGHT_TIMEOUT`) stands in for N
    per-call timeouts on a down server. Reuses the doctor reachability + pulled-
    model logic. Returns True (proceed) for the claude provider untouched.
    """
    cfg = plugin.enrichment
    if cfg.provider != "ollama":
        return True

    from daily_driver.integrations import ollama_client

    endpoint = ai.ollama.endpoint
    model = cfg.model or ollama_client.DEFAULT_MODEL
    try:
        pulled = ollama_client.list_models(endpoint, timeout=_OLLAMA_PREFLIGHT_TIMEOUT)
    except ollama_client.OllamaNotReachableError:
        Console.warning(
            f"ollama not reachable at {endpoint} -- LLM enrichment skipped this "
            "run; start the server (ollama serve) and run jobs run --backfill to "
            "fill these rows"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        Console.warning(
            f"ollama at {endpoint} returned an error ({exc}) -- LLM enrichment "
            "skipped this run; run jobs run --backfill once it is healthy"
        )
        return False

    if model not in pulled:
        Console.warning(
            f"ollama model {model!r} not pulled at {endpoint} -- LLM enrichment "
            f"skipped this run; pull it (ollama pull {model}) and run jobs run "
            "--backfill to fill these rows"
        )
        return False
    return True


# ── Notification ─────────────────────────────────────────────────────────────


def _notify_new_jobs(count: int, csv_path: Path) -> None:
    desktop_notify(
        "Job Scraper",
        f"{count} new jobs found",
        open_url=csv_path.as_uri(),
        subtitle=csv_path.name,
    )


# ── Public entry points ──────────────────────────────────────────────────────


def run_backfill(
    plugin: JobSearchPlugin,
    csv_path: Path,
    ephemeral_dir: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
) -> None:
    """Re-enrich empty fields in an existing jobs.csv."""
    from daily_driver.plugins.job_search.scraper.csv_io import backfill

    ctx = ScrapeContext(plugin=plugin, ai=ai or AIConfig(), context_text=context_text)
    backfill(ctx, csv_path, ephemeral_dir)


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


def _empty_product_stats() -> dict[str, int]:
    return {"enriched": 0, "skipped_cached": 0, "failed": 0}


def _empty_fit_stats() -> dict[str, int]:
    return {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}


def _run_llm_enrichment(
    plugin: JobSearchPlugin,
    ai_cfg: AIConfig,
    typed_jobs: list[EnrichedJob],  # noqa: F821
    ctx: ScrapeContext,
    sink: _JobSink,
    product_phase: Phase,
    fit_phase: Phase,
    *,
    run_preflight: bool,
    product_budget: int = 0,
    fit_budget: int = 0,
) -> tuple[dict[str, int], dict[str, int]]:
    """Run the overlapped product + fit/notes LLM pass, flushing as it goes.

    Returns ``(product_stats, fn_stats)``. ``run_preflight`` gates the ollama
    reachability probe -- run it before the FIRST wave only; later waves skip it
    (the server state cannot change mid-run). On a failed probe the LLM phases
    render as zero-count done bars and zero counters (the same shape
    ``--no-enrich`` records), so the manifest stays honest and the user fills the
    rows later with ``jobs run --backfill``. The claude provider is untouched
    (its which-guard inside the enrichers already covers a missing CLI).
    ``sink.flush`` is the periodic resilience hook, invoked every ~25 results on
    the coordinator thread. ``product_budget`` / ``fit_budget`` are the SHARED
    running totals' remaining allowance for this wave (0 = use the config cap).
    """
    from daily_driver.plugins.job_search.scraper.enrichment import (
        enrich_product_and_fit_concurrently,
    )

    run_llm = True
    if run_preflight and _llm_enrichment_requested(plugin):
        run_llm = _ollama_enrichment_preflight(plugin, ai_cfg)

    if run_llm:
        # Product and fit/notes overlap under one shared concurrency cap (F1):
        # both fan out through a single executor bounded by the provider's
        # max_parallel, so total claude/ollama subprocesses never exceed it.
        product_phase.start()
        fit_phase.start()
        _, product_stats, fn_stats = enrich_product_and_fit_concurrently(
            typed_jobs,
            ctx,
            product_budget=product_budget,
            fit_budget=fit_budget,
            product_progress=product_phase.advance,
            fit_progress=fit_phase.advance,
            flush=sink.flush,
        )
    else:
        product_stats = _empty_product_stats()
        fn_stats = _empty_fit_stats()
    product_phase.done(
        f"{product_stats['enriched']} enriched, "
        f"{product_stats['skipped_cached']} cached, "
        f"{product_stats['failed']} failed"
    )
    fit_phase.done(
        f"{fn_stats['enriched']} enriched, "
        f"{fn_stats['skipped_budget']} skipped (budget), "
        f"{fn_stats['failed']} failed"
    )
    return product_stats, fn_stats


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
    product_budget: int,
    fit_budget: int,
    set_phase: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, int], dict[str, int]]:
    """Run one enrichment wave (detail + LLM) over ``jobs`` in place.

    ``wave_label`` suffixes the phase rows ("" for the only/first wave, e.g.
    " (wave 2)" for the post-Apple wave) so the live display shows each wave's
    own phase rows -- the honest two-wave UI. ``product_budget`` / ``fit_budget``
    are this wave's remaining slice of the shared running totals (0 = config
    cap). ``set_phase`` records the coarse run phase for the manifest. Flushes
    per phase and on the periodic hook; the caller's try/except flushes once more
    on interrupt. Returns ``(detail_stats, product_stats, fn_stats)``.
    """
    from daily_driver.plugins.job_search.scraper.enrichment import enrich_job_details
    from daily_driver.plugins.job_search.scraper.enrichment.detail import (
        render_detail_summary,
    )

    total = len(jobs)
    detail_phase = enrich_group.phase(f"Detail pages{wave_label}", total=total)
    product_phase = enrich_group.phase(f"Company products{wave_label}", total=total)
    fit_phase = enrich_group.phase(f"Fit and notes{wave_label}", total=total)

    if set_phase is not None:
        set_phase("detail")
    detail_phase.start()
    _, detail_stats = enrich_job_details(jobs, ctx, progress=detail_phase.advance)
    detail_phase.done(render_detail_summary(detail_stats))
    sink.flush()  # persist detail comp/posted_date before the LLM phases
    if set_phase is not None:
        set_phase("enrichment")

    product_stats, fn_stats = _run_llm_enrichment(
        plugin,
        ai_cfg,
        jobs,
        ctx,
        sink,
        product_phase,
        fit_phase,
        run_preflight=run_preflight,
        product_budget=product_budget,
        fit_budget=fit_budget,
    )
    return detail_stats, product_stats, fn_stats


def _wave_attempts(stats: dict[str, Any]) -> int:
    """LLM-call attempts a wave consumed (for shared-budget accounting).

    Every targeted job lands as either enriched or failed; budget-skipped jobs
    were never attempted. So attempts = enriched + failed -- the count to deduct
    from the next wave's remaining shared budget.
    """
    return int(stats.get("enriched", 0)) + int(stats.get("failed", 0))


def _add_stats(into: dict[str, int], more: dict[str, int]) -> None:
    """Accumulate one enrichment stats dict into a running total in place.

    Two-wave runs sum each wave's counters so the end-of-run reconciliation and
    manifest report the combined total, not the last wave alone.
    """
    for key, value in more.items():
        into[key] = into.get(key, 0) + value


def _accumulate_enrich_stats(
    product_stats: dict[str, int],
    fn_stats: dict[str, int],
    wave_result: dict[str, dict[str, Any]],
) -> None:
    """Fold a completed wave's product/fit stats into the running totals."""
    if "product" in wave_result:
        _add_stats(product_stats, wave_result["product"])
    if "fit" in wave_result:
        _add_stats(fn_stats, wave_result["fit"])


def _write_run_manifest(
    output_dir: Path,
    *,
    started_at: datetime,
    all_jobs: list[dict[str, Any]],
    failed_sources: list[str],
    written: int,
    fn_enriched: int,
    product_enriched: int,
    phase_reached: str,
    interrupted: bool,
) -> None:
    """Write jobs-last-run.json. Written on BOTH the happy and interrupt paths.

    ``phase_reached`` (scraping|detail|enrichment|complete) and ``interrupted``
    let ``jobs status`` surface a recovery line when a run was cut short, so the
    user knows to finish enrichment with ``jobs run --backfill``.
    """
    run_manifest = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "sources_ok": sorted(
            {
                j.get("source", "")
                for j in all_jobs
                if j.get("source") and j.get("source") not in failed_sources
            }
        ),
        "sources_failed": failed_sources,
        "new_jobs": written,
        "enriched_fit_notes": fn_enriched,
        "enriched_product": product_enriched,
        "phase_reached": phase_reached,
        "interrupted": interrupted,
    }
    last_run_path = output_dir / "jobs-last-run.json"
    try:
        last_run_path.write_text(
            json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8"
        )
        log.debug("Run manifest written to %s", last_run_path)
    except OSError as exc:
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
) -> int:
    """Run all enabled scrapers and append new rows to ``output_dir/jobs.csv``.

    Returns the process-style exit code (0 success, 1 on failed sources /
    I/O error). Performs no argparse or ``sys.exit()`` — the CLI layer is
    responsible for exit handling.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        load_existing_jobs,
    )

    started_at = datetime.now(timezone.utc)
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

    with file_lock(lock_path):
        known_urls, known_keys, header = load_existing_jobs(csv_path)

        # Union archive-table dedup state so triaged listings (pruned to
        # jobs.archive.csv) are never re-discovered.
        archive_urls, archive_keys = load_archive_dedup(csv_path)
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
                    return 1

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

    # Coarse run phase for the manifest (scraping -> detail -> enrichment ->
    # complete). On a graceful stop the partially-reached phase tells
    # `jobs status` what to recover. A list so closures (incl. the wave-1
    # thread) can update it.
    phase_reached = ["scraping"]

    title = "Job search run (dry-run)" if dry_run else "Job search run"
    # The live block renders on any TTY; verbosity controls only how much scrolls
    # above it. enlighten pins the bars in a terminal scroll region and the
    # terminal scrolls log lines above them, so WARN+ (and INFO/DEBUG heartbeats
    # under -v/-vv) stream live as the run progresses while the block stays
    # pinned. Non-TTY (cron/pipe) -- and an unresponsive TTY -- get plain line
    # mode, no block. Quiet mode ("errors only") suppresses the block entirely;
    # the plain-mode progress lines are dropped by RunProgress under quiet too.
    tty = Console.is_tty() and not Console.quiet_mode
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
            for sid in sids:
                _row(sid)

        def _on_started(sid: str) -> None:
            _row(sid).start(note=_slow_source_note(sid))

        def _on_progress(sid: str, completed: int, total: int | None) -> None:
            # Fed from each scraper's ctx.report(done, total) callback; upgrades the row to a live bar.
            _row(sid).progress(completed, total)

        def _on_done(sid: str, ok: bool, detail: str) -> None:
            _row(sid).finish(ok, detail)

        # The sink IS the durable-record checkpoint: each source's rows are
        # deduped/filtered/lifted and appended to jobs.csv as it lands (the
        # append-as-completed callback below). dry-run keeps the old in-memory
        # single pass with no sink and no writes.
        sink: _JobSink | None = None
        on_source_result: Callable[[str, list[dict[str, Any]]], object] | None = None
        if not dry_run:
            sink = _JobSink(
                csv_path=csv_path,
                lock_path=lock_path,
                header=header,
                known_urls=known_urls,
                known_keys=known_keys,
                plugin=plugin,
            )
            on_source_result = sink.append_source

        # Overlap state (Stage 3). When a phase 2 (Apple) follows phase 1, the
        # phase-1 rows are enriched in a background WAVE while Apple still
        # scrapes; Apple's rows get a second wave afterward. Budgets are SHARED
        # running totals across waves. Enrichment runs on the wave-1 thread here,
        # not the main thread, so the per-enricher SIGINT notifier no-ops (main-
        # thread only) -- the run-level signal handler covers interrupts instead.
        enrich_cfg = plugin.enrichment
        do_enrich = not (dry_run or no_enrich)

        def _set_phase(p: str) -> None:
            phase_reached[0] = p

        enrich_group: Group | None = None
        wave1_thread: threading.Thread | None = None
        wave1_result: dict[str, dict[str, Any]] = {}
        wave1_error: list[BaseException] = []
        wave1_count = [0]  # phase-1 rows handed to wave 1

        def _run_wave1(phase1_jobs: list[EnrichedJob]) -> None:
            assert sink is not None and enrich_group is not None
            try:
                d, p, f = _enrich_wave(
                    plugin,
                    ai_cfg,
                    phase1_jobs,
                    ctx,
                    sink,
                    enrich_group,
                    wave_label="",
                    run_preflight=True,
                    product_budget=0,  # wave 1 gets the full config budget
                    fit_budget=0,
                    set_phase=_set_phase,
                )
                wave1_result.update({"detail": d, "product": p, "fit": f})
            except BaseException as exc:  # noqa: BLE001 -- relayed to main thread
                wave1_error.append(exc)

        def _on_phase1_done(has_phase2: bool) -> None:
            # Only overlap when an Apple phase actually follows -- otherwise the
            # single post-scrape wave on the main thread is simpler and keeps the
            # interrupt notifier live.
            if not (do_enrich and has_phase2) or sink is None:
                return
            nonlocal enrich_group, wave1_thread
            with sink._rows_lock:
                phase1_jobs = list(sink.rows)
            wave1_count[0] = len(phase1_jobs)
            enrich_group = rp.group("Enriching jobs")
            wave1_thread = threading.Thread(
                target=_run_wave1, args=(phase1_jobs,), daemon=True
            )
            wave1_thread.start()

        all_jobs, failed_sources, source_results = run_all_scrapers(
            ctx,
            sources_override=sources_override,
            on_sources_enabled=_on_enabled,
            on_source_start=_on_started,
            on_source_progress=_on_progress,
            on_source_done=_on_done,
            on_note=rp.note,
            on_source_result=on_source_result,
            on_phase1_done=_on_phase1_done,
            # Force Apple headless while the live block owns the terminal.
            force_headless=tty,
        )

        if failed_sources:
            log.warning("Failed sources: %s", ", ".join(failed_sources))

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

        product_stats = _empty_product_stats()
        fn_stats = _empty_fit_stats()
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
            # place and flushes jobs.csv per completed phase, every ~25 LLM
            # results, and once more on interrupt. A crash mid-enrichment loses
            # at most one flush window; jobs run --backfill resumes the rest.
            assert sink is not None  # not dry_run -> sink exists
            try:
                # Wave 1 (phase-1 rows) may already be running in a background
                # thread (started at on_phase1_done). Join it, then enrich the
                # remaining (Apple) rows as wave 2 with the budget wave 1 left.
                if wave1_thread is not None:
                    wave1_thread.join()
                    if wave1_error:
                        raise wave1_error[0]
                    assert enrich_group is not None
                    _accumulate_enrich_stats(product_stats, fn_stats, wave1_result)
                    # Wave 2 receives the WHOLE row list (so its slot mutations
                    # and the periodic flush both target sink.rows directly); the
                    # already-enriched phase-1 rows are skipped as ineligible, so
                    # only the newly-landed Apple rows are actually enriched.
                    if len(typed_jobs) > wave1_count[0]:
                        fit_used = _wave_attempts(wave1_result.get("fit", {}))
                        prod_used = _wave_attempts(wave1_result.get("product", {}))
                        _, p2, f2 = _enrich_wave(
                            plugin,
                            ai_cfg,
                            typed_jobs,
                            ctx,
                            sink,
                            enrich_group,
                            wave_label=" (wave 2)",
                            run_preflight=False,  # wave 1 already probed
                            product_budget=max(
                                1, enrich_cfg.max_enrich_companies - prod_used
                            ),
                            fit_budget=max(1, enrich_cfg.max_enrich_fit - fit_used),
                            set_phase=_set_phase,
                        )
                        _add_stats(product_stats, p2)
                        _add_stats(fn_stats, f2)
                else:
                    # No overlap (no Apple phase, or it landed nothing): one wave
                    # over every row on the main thread, same as the classic path.
                    enrich_group = rp.group("Enriching jobs")
                    _, p1, f1 = _enrich_wave(
                        plugin,
                        ai_cfg,
                        typed_jobs,
                        ctx,
                        sink,
                        enrich_group,
                        wave_label="",
                        run_preflight=True,
                        product_budget=0,
                        fit_budget=0,
                        set_phase=_set_phase,
                    )
                    _add_stats(product_stats, p1)
                    _add_stats(fn_stats, f1)
            except KeyboardInterrupt:
                # Persist whatever enrichment landed before the interrupt so a
                # ^C/SIGTERM never discards a partial enrichment pass, then
                # record the interruption in the manifest for the recovery line.
                sink.flush()
                _write_run_manifest(
                    output_dir,
                    started_at=started_at,
                    all_jobs=all_jobs,
                    failed_sources=failed_sources,
                    written=len(sink.rows),
                    fn_enriched=fn_stats["enriched"],
                    product_enriched=product_stats["enriched"],
                    phase_reached=phase_reached[0],
                    interrupted=True,
                )
                raise
            if enrich_group is not None:
                enrich_group.done()

    n = len(typed_jobs)
    log.info(
        "Fit+Notes enriched: %d/%d, %d skipped (budget), %d failed (parse/subprocess)",
        fn_stats["enriched"],
        n,
        fn_stats["skipped_budget"],
        fn_stats["failed"],
    )
    log.info(
        "Product enriched: %d/%d, %d skipped (cached), %d failed",
        product_stats["enriched"],
        n,
        product_stats["skipped_cached"],
        product_stats["failed"],
    )
    # Headline that reconciles every job: found (raw scraped) -> new (not already
    # in csv) -> matched location (the only filter that removes jobs). Intra/
    # cross-run duplicates are the remainder. `funnel` (the per-source breakdown)
    # was computed above while the live bars were up; it is reused here.
    summary = (
        f"Completed: {raw_found} found -> {pre_filter} new "
        f"-> {len(typed_jobs)} matched location"
    )
    if filtered:
        summary += f" ({filtered} skipped by location)"
    Console.info(summary)
    if funnel:
        width = max(len(_display_name(sid)) for sid in funnel)
        for sid, c in funnel.items():
            # The grey "other" bar segment (within-run duplicates + url-less rows)
            # has no count of its own in the funnel; surface it here only when
            # present so no datum lives in colour alone.
            other = _funnel_other(c)
            line = (
                f"  {_display_name(sid):<{width}}  {c['found']} found, "
                f"{c['new']} new, {c['known']} already in csv, "
                f"{c['loc_skip']} skipped (location)"
            )
            if other > 0:
                line += f", {other} other"
            Console.info(line)

    if dry_run:
        Console.success(
            f"Dry-run complete: {len(typed_jobs)} new jobs ready (nothing written)."
        )
        _print_dry_run_table(typed_jobs)
        return 1 if failed_sources else 0

    # Rows were already appended per-source during scraping (the durable-record
    # checkpoint). Persist the in-place enrichment with one final rewrite through
    # the backfill rewrite path; the lock is taken only for the rewrite, never
    # across the LLM calls, so a concurrent prune/backfill is serialized but not
    # starved. --no-enrich leaves the rows as appended (no rewrite needed).
    assert sink is not None  # dry_run returned above; non-dry-run always has a sink
    written = len(sink.rows)
    if not no_enrich:
        sink.flush()
    skip_note = " (enrichment skipped)" if no_enrich else ""
    Console.success(
        f"Scraper complete: {written} new jobs appended to {csv_path}.{skip_note}"
    )

    phase_reached[0] = "complete"
    _write_run_manifest(
        output_dir,
        started_at=started_at,
        all_jobs=all_jobs,
        failed_sources=failed_sources,
        written=written,
        fn_enriched=fn_stats["enriched"],
        product_enriched=product_stats["enriched"],
        phase_reached=phase_reached[0],
        interrupted=False,
    )

    if failed_sources:
        log.error("Scraper failures: %s", ", ".join(failed_sources))
        return 1

    if written > 0:
        _notify_new_jobs(written, csv_path)
    return 0
