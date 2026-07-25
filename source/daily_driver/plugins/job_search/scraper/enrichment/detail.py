"""HTTP detail-page enricher: comp/description from per-job detail pages (no LLM)."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from daily_driver.core.logging import get_logger
from daily_driver.core.progress import ProgressCallback
from daily_driver.plugins.job_search.scraper.models import (
    ENRICH_ELIGIBLE_STATUSES,
    EnrichedJob,
)
from daily_driver.plugins.job_search.scraper.parsing import (
    _parse_detail_page,
    comp_from_text,
)
from daily_driver.plugins.job_search.scraper.sources._http import (
    Session,
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.context import ScrapeContext

log = get_logger(__name__)

# Worker cap for the detail-fetch pool. Small on purpose: detail fetches are
# politeness-throttled per host, so a handful of workers lets distinct hosts
# proceed concurrently without hammering any single one.
_MAX_DETAIL_WORKERS = 4


#: The fields a detail fetch can fill on a row.
DETAIL_FIELDS = frozenset({"comp", "description"})


@dataclass(frozen=True)
class HostCapability:
    """Which fields a host's detail page can serve, and why when it's none.

    ``reason`` is the honest, host-specific phrase surfaced verbatim in the
    phase summary when ``fields`` is empty -- replacing the old catch-all
    "blocked host", which hid three unrelated causes (no JSON-LD, bot-wall,
    rate-limit) behind one label.
    """

    fields: frozenset[str]
    reason: str = ""


# Per-host deviations from the JSON-LD default, each for its own reason:
# Indeed 403s bare requests; Hacker News 429s /item?id=*. LinkedIn guest
# pages have no JSON-LD anonymously but server-render a salary card
# (``compensation__salary``) that JobSpy discards, so it serves comp only —
# ``_parse_detail_page`` routes it to a LinkedIn-specific parser (verified
# live 2026-07-24); LinkedIn descriptions come from JobSpy at scrape time.
_HOST_CAPABILITY: dict[str, HostCapability] = {
    "indeed.com": HostCapability(frozenset(), "indeed: bot-walled"),
    "news.ycombinator.com": HostCapability(frozenset(), "hn: rate-limited"),
    # Apple details pages are a client-rendered SPA: the server HTML carries no
    # JSON-LD JobPosting and no description prose (the body loads via an
    # authenticated api/v1 call). The generic fetch can never recover it, so
    # skip it rather than spend a request and mislabel the miss (verified live
    # 2026-07-02).
    "jobs.apple.com": HostCapability(frozenset(), "apple: SPA, no server JSON-LD"),
    # Greenhouse hosted pages sit behind volume-based bot protection on ONE
    # shared host (job-boards.greenhouse.io serves every board), so a
    # discovery-scale run gets 403s after the first requests (verified live
    # 2026-07-04: single requests return 200 with any UA). The pages carry no
    # JSON-LD anyway; comp comes from the scraped description via the
    # comp-from-text pre-pass, and the description itself comes from the API
    # at scrape time. Matches boards.greenhouse.io too (same protection, same
    # data already in hand).
    "greenhouse.io": HostCapability(frozenset(), "greenhouse: comp from scrape"),
    "linkedin.com": HostCapability(frozenset({"comp"})),
}
_DEFAULT_CAPABILITY = HostCapability(DETAIL_FIELDS)


def _capability_for(url: str) -> HostCapability:
    """Host capability for a URL; unknown hosts default to full JSON-LD."""
    host = urlsplit(url).netloc
    for pattern, capability in _HOST_CAPABILITY.items():
        # Exact/suffix match, never substring: "evillinkedin.com" and
        # "linkedin.com.evil.example" must not inherit a host's capability.
        if host == pattern or host.endswith("." + pattern):
            return capability
    return _DEFAULT_CAPABILITY


def comp_recompute_value(job: EnrichedJob, *, force: bool) -> str:
    """Return the comp the cached description would set on ``job``, or "".

    The single definition of the comp fill/repair rule, shared by the detail
    pre-pass and ``_backfill_needs`` (dry-run count and short-circuit) so the
    preview can never drift from what the pass actually does. Overwrites
    happen only under ``force``; a description that yields nothing never
    blanks an existing value.
    """
    if not job.description_text or job.status not in ENRICH_ELIGIBLE_STATUSES:
        return ""
    if job.comp and not force:
        return ""
    text_comp = comp_from_text(job.description_text)
    if text_comp and text_comp != job.comp:
        return text_comp
    return ""


def needs_description_fetch(job: EnrichedJob, fetch_descriptions: bool) -> bool:
    """Whether a detail fetch could heal ``job``'s missing description: the
    caller allows it, the row lacks one, and the URL's host serves them.

    The single predicate behind the backlog admission gate and the backfill
    needs count, so the two can never drift: Workable/Workday rows heal;
    LinkedIn-legacy and Apple rows stay out of pointless fetch loops.
    """
    url = (job.url or "").strip()
    return (
        fetch_descriptions
        and not job.description_text.strip()
        and bool(url)
        and "description" in _capability_for(url).fields
    )


def _row_needs(job: EnrichedJob, fill_fields: frozenset[str]) -> frozenset[str]:
    """Fields a detail fetch would fill on ``job``: missing ∩ host-capable ∩
    caller-enabled.

    The one rule that decides every fetch — a row fetches exactly when this is
    non-empty. Fill-only: a field the row already has is never a need, so a
    fetch can never overwrite existing data.
    """
    missing = set()
    if not job.comp:
        missing.add("comp")
    if not job.description_text:
        missing.add("description")
    url = (job.url or "").strip()
    if not url:
        return frozenset()
    return frozenset(missing) & _capability_for(url).fields & fill_fields


def _skip_reason(job: EnrichedJob, fill_fields: frozenset[str]) -> str | None:
    """Classify why a job needs no detail fetch, or None when it should fetch.

    Reasons are short, user-facing phrases reused verbatim in the phase summary
    so the breakdown reads plainly. "already complete" means the row lacks no
    field this pass could fill (its host's per-field capability included).
    """
    if job.status not in ENRICH_ELIGIBLE_STATUSES:
        return "inactive"
    url = (job.url or "").strip()
    if not url:
        return "no url"
    if _row_needs(job, fill_fields):
        return None
    capability = _capability_for(url)
    if not capability.fields:
        return capability.reason
    return "already complete"


def render_detail_summary(stats: dict[str, Any]) -> str:
    """Render the detail phase.done line with a per-reason skip breakdown.

    e.g. "0 enriched, 7 skipped (5 already complete, 2 indeed: bot-walled)". The
    per-reason counts in ``stats['skip_reasons']`` sum to ``stats['skipped']``.
    """
    base = f"{stats['enriched']} enriched"
    extras = []
    from_desc = stats.get("from_description") or 0
    if from_desc:
        extras.append(f"{from_desc} from cached descriptions")
    recomputed = stats.get("comp_recomputed") or 0
    if recomputed:
        extras.append(f"{recomputed} comp repaired")
    descriptions = stats.get("descriptions_filled") or 0
    if descriptions:
        extras.append(f"{descriptions} descriptions filled")
    if extras:
        base += f" ({', '.join(extras)})"
    base += f", {stats['skipped']} skipped"
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
    fill_fields: frozenset[str] = DETAIL_FIELDS,
    force: bool = False,
) -> tuple[list[EnrichedJob], dict[str, Any]]:
    """Fetch detail pages to fill each row's missing fields, needs-driven.

    A row fetches exactly when ``_row_needs`` is non-empty: a field is a need
    when the row lacks it, the URL's host can serve it (``_HOST_CAPABILITY``),
    and the caller asked for it (``fill_fields``). Writes are fill-only and
    gated the same way — a fetch never overwrites existing data, and a field
    outside ``fill_fields`` is never written even when the page returns it.
    Callers that must not write descriptions (the backfill and backlog paths,
    which rely solely on the sidecar cache) pass ``frozenset({"comp"})``.

    ``force`` re-runs the comp-from-description pre-pass on rows that already
    have ``comp`` and overwrites when the cached description yields a different
    value (a parser fix can correct previously mis-extracted rows). It never
    blanks an existing comp when the description yields nothing, never touches
    rows with no cached description, and triggers no extra HTTP — an
    unimproved row still skips as "already complete".

    Fetches run on a small thread pool (``min(4, n_hosts)`` workers) with
    per-host politeness: requests to the same host stay ``detail_delay_seconds``
    apart while different hosts proceed concurrently. Caches by URL within the
    run so jobs that share a detail URL only generate one HTTP request. Skips
    rows with no needs ("already complete"), inactive rows, url-less rows, and
    known bot-walled / rate-limited hosts.
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
    text_filled = 0
    comp_recomputed = 0
    for i, job in enumerate(out):
        # Comp is often already in the scraped description (pay-transparency
        # text); fill it from there first so the row needs no page fetch at
        # all. Same active-status gate as the fetch path -- this pass never
        # touched triaged rows before and still doesn't. A filled row counts
        # as enriched (not skipped): it gained data, it just cost no request.
        # (JobSpy rows get a scrape-time shot via its own extract_salary; the
        # `not job.comp` gate means this pass only sees what that one missed.)
        # Under ``force`` rows with existing comp re-parse too, but only a
        # non-empty, different result overwrites — a description that no
        # longer parses must not blank a previously extracted value.
        text_comp = (
            comp_recompute_value(job, force=force) if "comp" in fill_fields else ""
        )
        if text_comp:
            had_comp = bool(job.comp)
            out[i] = job.with_updates(comp=text_comp)
            if had_comp:
                comp_recomputed += 1
            else:
                text_filled += 1
            continue
        url = (job.url or "").strip()
        reason = _skip_reason(job, fill_fields)
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
    desc_filled = 0

    def _apply(idx: int, url: str, details: dict[str, Any]) -> None:
        nonlocal enriched_count, desc_filled
        job = out[idx]
        updates: dict[str, Any] = {}
        comp = details.get("comp", "") or ""
        if "comp" in fill_fields and comp and not job.comp:
            updates["comp"] = comp
        desc = details.get("description_text", "") or ""
        if "description" in fill_fields and desc and not job.description_text:
            updates["description_text"] = desc
            desc_filled += 1
        if updates:
            out[idx] = job.with_updates(**updates)
            enriched_count += 1
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
        "[detail] fetched %d pages, enriched %d of %d jobs "
        "(%d comp from cached descriptions, %d comp repaired)",
        fetched_count[0],
        enriched_count + text_filled + comp_recomputed,
        len(out),
        text_filled,
        comp_recomputed,
    )
    return out, {
        "fetched": fetched_count[0],
        "enriched": enriched_count + text_filled + comp_recomputed,
        "from_description": text_filled,
        "comp_recomputed": comp_recomputed,
        "descriptions_filled": desc_filled,
        "skipped": skipped_count,
        "skip_reasons": skip_reasons,
        "total": len(out),
    }
