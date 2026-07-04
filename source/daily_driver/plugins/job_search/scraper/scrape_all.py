"""Scraper fan-out: run every enabled source and merge the results.

The two-phase orchestrator (:func:`run_all_scrapers`), the per-source invoker
(:func:`_run_one`), cross-source dedup, the per-source funnel/display helpers,
and the source-classification constants. Consumed by :func:`runner._run_impl`;
tests monkeypatch ``SCRAPERS`` / ``scrape_jobspy`` on THIS module (the consumer).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any, cast

from daily_driver.core.logging import adopt_third_party_loggers, get_logger
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.context import (
    PartialSourceError,
    ScrapeContext,
    countries_list,
)
from daily_driver.plugins.job_search.scraper.models import BOARD_SOURCE_CANONICALS
from daily_driver.plugins.job_search.scraper.rows import dedup_key, location_matches
from daily_driver.plugins.job_search.scraper.sources import SCRAPERS
from daily_driver.plugins.job_search.scraper.sources._http import (
    HTTPError,
    HTTPTimeout,
)
from daily_driver.plugins.job_search.scraper.sources.jobspy import scrape_jobspy

log = get_logger(__name__)


# Sources that require a full (non-headless) browser. SourceToggle has
# extra="forbid" so a config-based `type: playwright` key is rejected by
# pydantic -- browser classification must live in code, not config.
_PLAYWRIGHT_SOURCES: frozenset[str] = frozenset({"apple"})

# Site-named sources backed by python-jobspy. Each enabled site is fetched in
# its own backend call under its own row (see _jobspy_scrape_plan); the library
# is an implementation detail, never part of the user surface.
_JOBSPY_SITES: tuple[str, ...] = ("linkedin", "indeed")

# Board-backed sources that checkpoint each finished board/account through
# ctx.checkpoint. The discovery expansion turned these into long multi-board
# walks (a full run enumerates ~1,200 discovered boards), so an interrupt or
# crash mid-loop would otherwise lose the whole source's scrape. Coincides
# with SOURCE_CAPABILITIES' enumeration="full" rows today, but deliberately
# NOT derived from that map (declaration-only; a new board source must be
# added there AND to models._BOARD_SOURCE_PREFIXES). Shares membership with
# the dedup source-upgrade preference -- both are facets of "full-enumeration
# board-backed source" -- so both read the models set.
_BOARD_CHECKPOINT_SOURCES: frozenset[str] = BOARD_SOURCE_CANONICALS

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

    # Sources that checkpoint per unit (the jobspy sites and the board
    # sources) append incrementally as they run, so the coordinator must SKIP
    # their end-of-source
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

        # The jobspy-backed (multi-hour) sites and the multi-board sources
        # checkpoint per unit; the fast single-call sources rely on the
        # end-of-source append (seconds of loss).
        def _checkpoint_for(
            row_id: str,
        ) -> Callable[[str, list[dict[str, Any]]], None] | None:
            if row_id in _JOBSPY_SITES or row_id in _BOARD_CHECKPOINT_SOURCES:
                return _checkpoint
            return None

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
