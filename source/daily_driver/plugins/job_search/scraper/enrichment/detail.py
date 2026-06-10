"""HTTP detail-page enricher: comp/posted_date from per-job detail pages (no LLM)."""

from __future__ import annotations

import datetime as dt
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from daily_driver.core.logging import get_logger
from daily_driver.core.progress import ProgressCallback
from daily_driver.plugins.job_search.scraper.models import (
    ENRICH_SKIP_STATUSES,
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


def enrich_job_details(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, int]]:
    """Fetch each job's detail page and fill comp/posted_date/description.

    Caches by URL within the run so jobs that share a detail URL only generate
    one HTTP request. Skips jobs that already have `comp` set so listing-card
    sources that already provide salary aren't clobbered. Network/parse errors
    are swallowed — missing data is the expected outcome for boards that don't
    expose JSON-LD, not an error worth aborting the run for.

    Returns ``(jobs, stats)``; replaces slots in the passed list with new frozen
    instances (the individual models are never mutated).
    """
    # Replace slots in the caller's list (not a copy) so a KeyboardInterrupt
    # mid-pass leaves enriched-so-far results for a caller that persists them
    # (backfill rewrites on interrupt; run() does not flush mid-run yet).
    out = jobs
    cache: dict[str, dict[str, Any]] = {}
    cfg = ctx.plugin.enrichment
    delay = cfg.detail_delay_seconds

    # Hosts whose detail pages we deliberately skip:
    # - linkedin.com: anonymous detail pages don't emit JSON-LD; JobSpy's
    #   linkedin_fetch_description already populates description.
    # - news.ycombinator.com: aggressive 429 rate-limiting on /item?id=*.
    #   Title-derived data from hn_who_is_hiring / hn_jobs is sufficient.
    #   A future Algolia /api/v1/items/{id} integration could replace this.
    # - *.indeed.com: bot-walls bare requests with 403; JobSpy already
    #   populates description for Indeed listings.
    hn_skipped_logged = False
    indeed_skipped_logged = False

    session: Session | None = None
    fetched_count = 0
    enriched_count = 0
    skipped_count = 0
    for i, job in enumerate(out):
        if job.comp:
            skipped_count += 1
            continue
        if job.status.value in ENRICH_SKIP_STATUSES:
            skipped_count += 1
            continue
        url = (job.url or "").strip()
        if not url:
            skipped_count += 1
            continue
        if "linkedin.com" in url:
            skipped_count += 1
            continue
        if "news.ycombinator.com" in url:
            skipped_count += 1
            if not hn_skipped_logged:
                log.debug(
                    "[detail] skipping HN detail enrichment "
                    "(rate-limited; title-derived data only)"
                )
                hn_skipped_logged = True
            continue
        if "indeed.com" in url:
            skipped_count += 1
            if not indeed_skipped_logged:
                log.debug(
                    "[detail] skipping Indeed detail enrichment "
                    "(JobSpy already populates description)"
                )
                indeed_skipped_logged = True
            continue

        if url in cache:
            details = cache[url]
        else:
            if fetched_count > 0 and delay > 0:
                time.sleep(delay)
            fetched_count += 1
            if session is None:
                session = _http_session(ctx)
            resp = _api_get(session, url, ctx, label="detail")
            if resp is None:
                details = {}
            else:
                try:
                    details = _parse_detail_page(resp.text, url)
                except ValueError as exc:
                    # Malformed page data (bad JSON-LD, unexpected shape) is
                    # non-fatal: missing comp just means the CSV stays blank.
                    # Programmer errors (AttributeError, TypeError) are
                    # deliberately NOT caught — those signal real regressions
                    # in `_parse_detail_page` and must remain visible.
                    log.warning("[detail] %s: parse failed: %s", url, exc)
                    details = {}
            cache[url] = details

        # Fill only blanks (comp was already confirmed blank by the skip above).
        updates: dict[str, Any] = {}
        comp = details.get("comp", "") or ""
        if comp:
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
            out[i] = job.with_updates(**updates)

        if progress is not None:
            progress(1, urlsplit(url).netloc or None)

    log.info(
        "[detail] fetched %d pages, enriched %d of %d jobs",
        fetched_count,
        enriched_count,
        len(out),
    )
    return out, {
        "fetched": fetched_count,
        "enriched": enriched_count,
        "skipped": skipped_count,
        "total": len(out),
    }
