"""Workable source: public account widget API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


def scrape_workable(ctx: ScrapeContext) -> list[dict]:
    """Scrape jobs from the Workable account widget API (public, no auth).

    Reads account slugs from config at
    job_search.sources.workable.workable_accounts (default: []). Each slug maps
    to https://apply.workable.com/api/v1/widget/accounts/{slug} which returns
    the account's listed postings in a single request.

    Unlike Ashby, the Workable payload carries the company name at the top level
    (``name``), so it is used directly; the slug is only the fallback. The
    widget list endpoint omits per-job descriptions, so ``description_text`` is
    emitted empty for the enrichment pass to fill later.
    """
    from daily_driver.plugins.job_search.config import WorkableToggle
    from daily_driver.plugins.job_search.scraper.roles import matches_roles
    from daily_driver.plugins.job_search.scraper.runner import (
        PartialSourceError,
        source_toggle,
    )

    accounts = source_toggle(ctx.plugin, "workable", WorkableToggle).workable_accounts
    session = _http_session(ctx)
    jobs: list[dict] = []
    # Accounts whose request failed -> the source is degraded (its result is
    # incomplete). An all-failed source returns [] otherwise indistinguishable
    # from a clean "no roles matched"; surfaced after the loop.
    failed_accounts: list[str] = []

    # Live progress unit: one account (reported at loop-top so a skipped account
    # still advances the bar).
    total = len(accounts)
    done = 0
    for slug in accounts:
        # Graceful-stop checkpoint between accounts: return what is matched so far.
        if ctx.stop_event.is_set():
            log.info("[workable] stop requested; keeping %d jobs so far", len(jobs))
            return jobs
        ctx.report(done, total)
        done += 1
        api_url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        resp = _api_get(session, api_url, ctx, label=f"workable/{slug}")
        if not resp:
            failed_accounts.append(slug)
            continue
        data = resp.json()

        account_jobs = data.get("jobs", [])
        # Raw listing pre role-filter for board-diff closure.
        ctx.record_enumeration(
            f"Workable ({slug})",
            {entry.get("url", "") for entry in account_jobs},
        )
        # Workable provides the company name; fall back to the slug only if absent.
        company_name = data.get("name") or slug.replace("-", " ").title()

        for entry in account_jobs:
            title = entry.get("title", "")
            if not title or not matches_roles(title, ctx.plugin):
                continue

            # No single location string in Workable; assemble from city/country.
            parts = [p for p in (entry.get("city"), entry.get("country")) if p]
            location = ", ".join(parts) or "Remote"

            jobs.append(
                {
                    "company": company_name,
                    "role": title,
                    "location": location,
                    "url": entry.get("url", ""),
                    "source": f"Workable ({slug})",
                    "date_found": today().isoformat(),
                    # Widget list payload has no per-job description text.
                    "description_text": "",
                }
            )

        log.info(
            "[workable] %s: %d jobs matched out of %d returned",
            slug,
            sum(1 for j in jobs if j["source"] == f"Workable ({slug})"),
            len(account_jobs),
        )

    if failed_accounts:
        # Incomplete scrape (one or more accounts failed) -> degraded, not a clean
        # run. The gathered jobs ride along and still append.
        raise PartialSourceError(
            jobs,
            f"{len(failed_accounts)} of {len(accounts)} accounts failed: "
            f"{', '.join(failed_accounts)}",
        )
    return jobs


__all__ = ["scrape_workable"]
