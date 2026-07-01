"""HTTP detail-page enricher: comp/posted_date from per-job detail pages (no LLM)."""

from __future__ import annotations

import datetime as dt
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from daily_driver.core.logging import get_logger
from daily_driver.core.progress import ProgressCallback
from daily_driver.plugins.job_search.scraper.models import (
    ENRICH_ELIGIBLE_STATUSES,
    EnrichedJob,
)
from daily_driver.plugins.job_search.scraper.parsing import _parse_detail_page
from daily_driver.plugins.job_search.scraper.sources._http import (
    Session,
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)

# Worker cap for the detail-fetch pool. Small on purpose: detail fetches are
# politeness-throttled per host, so a handful of workers lets distinct hosts
# proceed concurrently without hammering any single one.
_MAX_DETAIL_WORKERS = 4


def _skip_reason(job: EnrichedJob) -> str | None:
    """Classify why a job needs no detail fetch, or None when it should fetch.

    Reasons are short, user-facing phrases reused verbatim in the phase summary
    so the breakdown reads plainly. Order mirrors the original skip checks.
    """
    if job.comp:
        return "already complete"
    if job.status not in ENRICH_ELIGIBLE_STATUSES:
        return "inactive"
    url = (job.url or "").strip()
    if not url:
        return "no url"
    # linkedin: anonymous detail pages don't emit JSON-LD (JobSpy already fills
    # description). news.ycombinator.com: aggressive 429 on /item?id=*. indeed:
    # bot-walls bare requests with 403 (JobSpy already fills description).
    if "linkedin.com" in url or "news.ycombinator.com" in url or "indeed.com" in url:
        return "blocked host"
    return None


def render_detail_summary(stats: dict[str, Any]) -> str:
    """Render the detail phase.done line with a per-reason skip breakdown.

    e.g. "0 enriched, 7 skipped (5 already complete, 2 blocked host)". The
    per-reason counts in ``stats['skip_reasons']`` sum to ``stats['skipped']``.
    """
    base = f"{stats['enriched']} enriched, {stats['skipped']} skipped"
    reasons = stats.get("skip_reasons") or {}
    if not reasons:
        return base
    parts = ", ".join(f"{count} {reason}" for reason, count in reasons.items())
    return f"{base} ({parts})"


class _HostThrottle:
    """Per-host politeness gate: enforces >= ``delay`` seconds between requests
    to the SAME netloc while letting different hosts proceed concurrently.

    The lock is held only to RESERVE a slot (advance the host's next-allowed
    timestamp), never across the sleep. A worker then sleeps outside the lock
    until its reserved time, so a dominant host's backlog spaces only its own
    requests — it never parks workers that could be serving other hosts (no
    head-of-line blocking). One registry lock guards the timestamp map.
    """

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self._lock = threading.Lock()
        # host -> monotonic time at which this host's next request may fire.
        self._next_allowed: dict[str, float] = {}

    def wait(self, host: str) -> None:
        """Reserve this request's slot for ``host`` (advancing the host's
        next-allowed time by ``delay``), then sleep — outside the lock — until
        that slot. Concurrent same-host callers serialize via their reservations;
        different hosts never wait on each other."""
        if self._delay <= 0:
            return
        now = time.monotonic()
        with self._lock:
            # Reserve at the later of now / the host's next free slot, then push
            # the slot forward for whoever reserves next. Released immediately so
            # the sleep below blocks only this worker, not other hosts' workers.
            start = max(now, self._next_allowed.get(host, now))
            self._next_allowed[host] = start + self._delay
        wait_s = start - now
        if wait_s > 0:
            time.sleep(wait_s)


def _round_robin_by_host(
    targets: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Reorder (idx, url) targets round-robin across their hosts.

    Groups by netloc (preserving each host's original order), then takes one
    per host per pass. A dominant host's long run is spread out so its targets
    don't monopolize the worker pool ahead of sparser hosts.
    """
    groups: dict[str, list[tuple[int, str]]] = {}
    for idx, url in targets:
        groups.setdefault(urlsplit(url).netloc, []).append((idx, url))
    queues = list(groups.values())
    ordered: list[tuple[int, str]] = []
    longest = max((len(q) for q in queues), default=0)
    for col in range(longest):
        for q in queues:
            if col < len(q):
                ordered.append(q[col])
    return ordered


def enrich_job_details(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, Any]]:
    """Fetch each job's detail page and fill comp/posted_date/description.

    Fetches run on a small thread pool (``min(4, n_hosts)`` workers) with
    per-host politeness: requests to the same host stay ``detail_delay_seconds``
    apart while different hosts proceed concurrently. Caches by URL within the
    run so jobs that share a detail URL only generate one HTTP request. Skips
    jobs that already have ``comp`` (listing-card sources that provide salary),
    inactive jobs, url-less jobs, and known bot-walled / rate-limited hosts.
    Network/parse errors are swallowed — missing data is the expected outcome
    for boards that don't expose JSON-LD, not an error worth aborting for.

    Returns ``(jobs, stats)``; replaces slots in the passed list with new frozen
    instances (the individual models are never mutated). Slot replacement runs
    on the calling thread in the consumer loop, never in a worker.
    """
    # Replace slots in the caller's list (not a copy) so a KeyboardInterrupt
    # mid-pass leaves enriched-so-far results for a caller that persists them
    # (backfill rewrites on interrupt; run() flushes the sink per phase and
    # periodically, so partial results survive).
    out = jobs
    cfg = ctx.plugin.enrichment
    delay = cfg.detail_delay_seconds

    hn_skipped_logged = False
    indeed_skipped_logged = False
    skipped_count = 0
    skip_reasons: dict[str, int] = {}
    fetch_targets: list[tuple[int, str]] = []  # (slot index, url)
    for i, job in enumerate(out):
        url = (job.url or "").strip()
        reason = _skip_reason(job)
        if reason is not None:
            skipped_count += 1
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            if url and "news.ycombinator.com" in url and not hn_skipped_logged:
                log.debug(
                    "[detail] skipping HN detail enrichment "
                    "(rate-limited; title-derived data only)"
                )
                hn_skipped_logged = True
            elif url and "indeed.com" in url and not indeed_skipped_logged:
                log.debug(
                    "[detail] skipping Indeed detail enrichment "
                    "(JobSpy already populates description)"
                )
                indeed_skipped_logged = True
            continue
        fetch_targets.append((i, url))

    # url -> parsed detail dict; shared across workers, guarded by cache_lock.
    cache: dict[str, dict[str, Any]] = {}
    cache_lock = threading.Lock()
    fetched_count = [0]
    fetch_lock = threading.Lock()
    throttle = _HostThrottle(delay)
    session: Session | None = None
    session_lock = threading.Lock()

    def _get_session() -> Session:
        # One shared session for the run (urllib3's pool is thread-safe for the
        # concurrent GETs we issue); built lazily under a lock so the first
        # worker to need it wins the race without a redundant second build.
        nonlocal session
        with session_lock:
            if session is None:
                session = _http_session(ctx)
            return session

    def _fetch(url: str) -> dict[str, Any]:
        with cache_lock:
            if url in cache:
                return cache[url]
        # Politeness is per host: distinct hosts proceed concurrently, same-host
        # requests stay >= delay apart.
        throttle.wait(urlsplit(url).netloc)
        with fetch_lock:
            fetched_count[0] += 1
        resp = _api_get(_get_session(), url, ctx, label="detail")
        if resp is None:
            details: dict[str, Any] = {}
        else:
            try:
                details = _parse_detail_page(resp.text, url)
            except ValueError as exc:
                # Malformed page data (bad JSON-LD, unexpected shape) is
                # non-fatal: missing comp just means the CSV stays blank.
                # Programmer errors (AttributeError, TypeError) are deliberately
                # NOT caught — those signal real regressions in
                # `_parse_detail_page` and must remain visible.
                log.warning("[detail] %s: parse failed: %s", url, exc)
                details = {}
        with cache_lock:
            cache.setdefault(url, details)
            return cache[url]

    enriched_count = 0

    def _apply(idx: int, url: str, details: dict[str, Any]) -> None:
        nonlocal enriched_count
        job = out[idx]
        updates: dict[str, Any] = {}
        comp = details.get("comp", "") or ""
        if comp and not job.comp:
            updates["comp"] = comp
            enriched_count += 1
        posted = details.get("posted_date")
        if isinstance(posted, str):
            try:
                posted = dt.date.fromisoformat(posted)
            except ValueError:
                posted = None
        if isinstance(posted, dt.date) and job.posted_date is None:
            updates["posted_date"] = posted
        desc = details.get("description_text", "") or ""
        if desc and not job.description_text:
            updates["description_text"] = desc
        if updates:
            out[idx] = job.with_updates(**updates)
        if progress is not None:
            progress(1, urlsplit(url).netloc or None)

    n_hosts = len({urlsplit(url).netloc for _, url in fetch_targets})
    workers = max(1, min(_MAX_DETAIL_WORKERS, n_hosts)) if fetch_targets else 1
    # Interleave submission round-robin by host so a dominant host's long run of
    # targets can't fill every worker slot ahead of other hosts' work. Combined
    # with the lock-free throttle (sleep outside the lock), other hosts' fetches
    # reach a worker promptly instead of queueing behind the backlog.
    submit_order = _round_robin_by_host(fetch_targets)

    if fetch_targets and workers > 1:
        # `with` joins the pool on every exit path so a crash can't hang at
        # atexit; the KI branch still cancels pending work before the with-exit.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures: dict[Future[Any], tuple[int, str]] = {}
            for idx, url in submit_order:
                futures[pool.submit(_fetch, url)] = (idx, url)
            try:
                # Slot replacement happens here on the calling thread (never in a
                # worker), so out[] mutation is single-threaded like llm.py.
                for fut in as_completed(futures):
                    idx, url = futures[fut]
                    _apply(idx, url, fut.result())
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                for fut in futures:
                    if fut.done() and not fut.cancelled():
                        idx, url = futures[fut]
                        # A drain-time apply error must not replace the
                        # KeyboardInterrupt: swallow + continue so KI re-raises.
                        try:
                            _apply(idx, url, fut.result())
                        except Exception as exc:  # noqa: BLE001
                            log.warning(
                                "[detail] %s: dropping result during interrupt "
                                "drain (%s)",
                                url,
                                exc,
                            )
                raise
    else:
        for idx, url in fetch_targets:
            _apply(idx, url, _fetch(url))

    log.info(
        "[detail] fetched %d pages, enriched %d of %d jobs",
        fetched_count[0],
        enriched_count,
        len(out),
    )
    return out, {
        "fetched": fetched_count[0],
        "enriched": enriched_count,
        "skipped": skipped_count,
        "skip_reasons": skip_reasons,
        "total": len(out),
    }
