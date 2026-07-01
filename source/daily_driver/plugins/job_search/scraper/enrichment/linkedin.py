"""LinkedIn description backfill: fill missing descriptions for the fit/notes pass.

JobSpy fills most LinkedIn descriptions at scrape time and the sidecar
(``descriptions.jsonl``) persists them across runs, so this phase's job is
recovering the remainder: legacy rows scraped before the sidecar existed, or
rows a prior run never covered. LinkedIn's anonymous detail pages emit no
JSON-LD, so the generic detail phase skips them outright. This phase always
runs alongside fit/notes enrichment and fetches the login-free
``/jobs/view/<id>`` page (the same one JobSpy uses), parsing
``div.show-more-less-html__markup`` so populated descriptions feed Fit/Notes in
the SAME wave. It is fill-missing-only, bounded to the fit/notes budget (the rows
``fit_notes_target_indices`` selects), politely throttled, and degrades
gracefully: a signup-wall redirect or parse miss leaves the row untouched, and
LinkedIn fetching stops after a run of consecutive failures so anonymous traffic
isn't hammered into 429s.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.enrichment.detail import _HostThrottle
from daily_driver.plugins.job_search.scraper.models import EnrichedJob
from daily_driver.plugins.job_search.scraper.parsing import parse_linkedin_description
from daily_driver.plugins.job_search.scraper.sources._http import (
    Session,
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.core.progress import ProgressCallback
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)

_LINKEDIN_HOST = "www.linkedin.com"
_LINKEDIN_VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}"
# Every LinkedIn job URL is `.../jobs/view/<numeric-id>`, so the id is reliably
# extractable regardless of the surrounding path/query.
_LINKEDIN_JOB_ID = re.compile(r"/jobs/view/(\d+)")
# Stop LinkedIn fetching after this many consecutive failures: once LinkedIn
# decides to wall anonymous traffic it walls all of it, so continuing only
# invites 429s without filling anything.
_MAX_CONSECUTIVE_FAILURES = 5


def _linkedin_job_id(url: str) -> str | None:
    """Numeric job id from a LinkedIn ``/jobs/view/<id>`` URL, else ``None``.

    Returns ``None`` for non-LinkedIn hosts and for LinkedIn URLs that don't
    carry a view id, so callers can skip rows that can't be canonicalized.
    """
    if not url or "linkedin.com" not in url:
        return None
    match = _LINKEDIN_JOB_ID.search(url)
    return match.group(1) if match else None


def _linkedin_headers(ctx: ScrapeContext) -> dict[str, str]:
    """Browser-like request headers the login-free LinkedIn page expects."""
    return {
        "authority": _LINKEDIN_HOST,
        "accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "upgrade-insecure-requests": "1",
        "user-agent": ctx.plugin.scraper.user_agent,
    }


def render_linkedin_summary(stats: dict[str, Any]) -> str:
    """Render the LinkedIn phase ``.done()`` line, mirroring render_detail_summary.

    e.g. "3 filled, 12 skipped, 2 walled". Appends "(stopped early)" when the
    consecutive-failure guard cut the phase short.
    """
    base = (
        f"{stats['filled']} filled, {stats['skipped']} skipped, "
        f"{stats['signup_walled']} walled"
    )
    if stats.get("stopped_early"):
        return f"{base} (stopped early)"
    return base


def fetch_linkedin_descriptions(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    target_idx: list[int],
    *,
    progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, Any]]:
    """Fill missing descriptions for the ``target_idx`` LinkedIn rows in place.

    ``target_idx`` is the SAME slice the fit/notes pass will enrich this wave
    (from ``fit_notes_target_indices``), so a filled description feeds the
    fit/notes call that follows. Sequential by design (one host); reuses the
    detail phase's per-host throttle and the shared HTTP seam. Replaces slots in
    the passed list with new frozen instances; the models are never mutated.

    Returns ``(jobs, stats)`` where stats carries ``fetched, filled, skipped,
    signup_walled, stopped_early, total``.
    """
    out = jobs
    cfg = ctx.plugin.enrichment
    stats: dict[str, Any] = {
        "fetched": 0,
        "filled": 0,
        "skipped": 0,
        "signup_walled": 0,
        "stopped_early": False,
        "total": len(target_idx),
    }

    throttle = _HostThrottle(cfg.detail_delay_seconds)
    headers = _linkedin_headers(ctx)
    session: Session | None = None
    consecutive_failures = 0

    for i in target_idx:
        job = out[i]
        # Fill-missing-only: a row that already carries a description never
        # generates a request (the common, low-volume case on a re-run).
        if job.description_text.strip():
            stats["skipped"] += 1
            continue
        job_id = _linkedin_job_id(job.url or "")
        if job_id is None:
            stats["skipped"] += 1
            continue
        url = _LINKEDIN_VIEW_URL.format(job_id=job_id)
        if session is None:
            session = _http_session(ctx)
        throttle.wait(_LINKEDIN_HOST)
        stats["fetched"] += 1
        resp = _api_get(session, url, ctx, label="linkedin", headers=headers)
        # A None response (terminal HTTP failure) or a redirect to the signup
        # wall both mean "no description for this row" -- count it, leave the row
        # untouched, and bail out of the whole phase once they pile up.
        if resp is None or "linkedin.com/signup" in (resp.url or ""):
            stats["signup_walled"] += 1
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                stats["stopped_early"] = True
                log.info(
                    "[linkedin] stopping after %d consecutive failures "
                    "(anonymous fetches walled)",
                    consecutive_failures,
                )
                break
            continue
        consecutive_failures = 0
        try:
            details = parse_linkedin_description(resp.text)
        except ValueError as exc:
            # Malformed page data is non-fatal: a missed description just leaves
            # Notes empty, the pre-existing outcome for LinkedIn rows.
            log.warning("[linkedin] %s: parse failed: %s", url, exc)
            details = {}
        desc = details.get("description_text", "") or ""
        # Match the top-of-loop skip gate (which uses .strip()): a whitespace-only
        # existing description must not block the fill, or this row would be
        # fetched every run yet never written.
        if desc and not out[i].description_text.strip():
            out[i] = out[i].with_updates(description_text=desc)
            stats["filled"] += 1
        if progress is not None:
            progress(1, _LINKEDIN_HOST)

    log.info(
        "[linkedin] fetched %d pages, filled %d of %d targeted rows",
        stats["fetched"],
        stats["filled"],
        stats["total"],
    )
    return out, stats


__all__ = [
    "fetch_linkedin_descriptions",
    "render_linkedin_summary",
]
