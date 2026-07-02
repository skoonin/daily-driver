"""RemoteOK source: public JSON API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.comp import _to_int
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)

# Endpoints queried per run. The unfiltered ``/api`` returns only the newest
# ~100 listings site-wide, where infra roles are sparse (observed live: 0 of
# 100), so on its own it routinely collects nothing relevant. The ``?tags=``
# views into the same listing set surface infra roles directly. Only RemoteOK's
# real tag slugs are used: ``devops``/``kubernetes``/``aws`` return focused
# filtered sets, whereas ``sre``/``platform``/``infrastructure`` return empty
# and ``cloud``/``ops``/``backend`` silently fall back to the unfiltered
# newest-100 firehose (verified live 2026-06-12). ``matches_roles`` is still the
# authority on what's kept, so a tag returning off-topic rows costs nothing but
# a request. The unfiltered endpoint stays first so a brand-new, as-yet-untagged
# infra posting is still caught.
_REMOTEOK_ENDPOINTS = (
    "https://remoteok.com/api",
    "https://remoteok.com/api?tags=devops",
    "https://remoteok.com/api?tags=kubernetes",
    "https://remoteok.com/api?tags=aws",
)


def scrape_remoteok(ctx: ScrapeContext) -> list[dict]:
    """Fetch jobs from RemoteOK's public JSON API.

    Queries the unfiltered listing feed plus a few focused ``?tags=`` views
    (see ``_REMOTEOK_ENDPOINTS``), dedupes by job id across them, and filters
    client-side with ``matches_roles()``. No auth or browser required.
    """
    from daily_driver.plugins.job_search.scraper.roles import matches_roles

    session = _http_session(ctx)
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    total = len(_REMOTEOK_ENDPOINTS)
    for done, url in enumerate(_REMOTEOK_ENDPOINTS):
        # Graceful-stop checkpoint between fetches: a Ctrl-C/SIGTERM during
        # scraping sets this on the main thread; stop issuing further requests
        # and keep whatever earlier endpoints already collected.
        if ctx.stop_event.is_set():
            log.info("[remoteok] stop requested; keeping %d jobs", len(jobs))
            break
        ctx.report(done, total)

        resp = _api_get(session, url, ctx, label="remoteok")
        if not resp:
            continue

        for item in resp.json():
            if "position" not in item:
                continue
            role = item["position"]
            if not matches_roles(role, ctx.plugin):
                continue
            job_id = str(item.get("id", ""))
            # Dedupe across endpoints: the same posting appears under multiple
            # tags and in the unfiltered feed.
            if job_id and job_id in seen_ids:
                continue
            if job_id:
                seen_ids.add(job_id)
            sal_min = _to_int(item.get("salary_min"))
            sal_max = _to_int(item.get("salary_max"))
            currency = item.get("salary_currency") or "USD"
            prefix = "$" if currency == "USD" else f"{currency} "
            comp = (
                f"{prefix}{sal_min:,}-{prefix}{sal_max:,}/yr"
                if sal_min is not None and sal_max is not None
                else ""
            )
            # The API ships the full posting body as HTML in ``description``;
            # strip tags so it feeds enrichment/scoring as plain text, matching
            # the greenhouse source. Absent on some rows -> left empty.
            desc_html = item.get("description", "")
            desc_text = (
                BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
                if desc_html
                else ""
            )
            job: dict = {
                "company": item.get("company", ""),
                "role": role,
                "location": item.get("location", "") or "Remote",
                "url": item.get("url", ""),
                "source": "RemoteOK",
                "date_found": today().isoformat(),
                "description_text": desc_text,
            }
            if comp:
                job["comp"] = comp
            jobs.append(job)

    log.info("[remoteok] %d jobs matched", len(jobs))
    return jobs


__all__ = ["scrape_remoteok"]
