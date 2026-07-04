"""WeWorkRemotely source: public per-category RSS feeds."""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.context import ScrapeContext

log = get_logger(__name__)


def scrape_weworkremotely(ctx: ScrapeContext) -> list[dict]:
    """Fetch jobs from WeWorkRemotely's public RSS feeds.

    WWR publishes per-category RSS at
    /categories/remote-{category}-jobs.rss. Categories are configured under
    plugins.job_search.sources.weworkremotely.wwr_categories in .dd-config.yaml.
    No auth or browser required.
    """
    from daily_driver.plugins.job_search.config import WeWorkRemotelyToggle
    from daily_driver.plugins.job_search.scraper.context import source_toggle
    from daily_driver.plugins.job_search.scraper.roles import matches_roles

    session = _http_session(ctx)
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    categories = source_toggle(
        ctx.plugin, "weworkremotely", WeWorkRemotelyToggle
    ).wwr_categories
    if not categories:
        log.warning("[weworkremotely] no wwr_categories configured; skipping")
        return jobs

    # Live progress unit: one category (reported at loop-top so a skipped
    # category still advances the bar).
    total = len(categories)
    done = 0
    for category in categories:
        # Graceful-stop checkpoint between categories: return what matched so far.
        if ctx.stop_event.is_set():
            log.info(
                "[weworkremotely] stop requested; keeping %d jobs so far", len(jobs)
            )
            return jobs
        ctx.report(done, total)
        done += 1
        url = f"https://weworkremotely.com/categories/remote-{category}-jobs.rss"
        resp = _api_get(session, url, ctx, label="weworkremotely")
        if not resp:
            continue

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            log.warning("[weworkremotely] RSS parse failed for %s: %s", category, exc)
            continue

        for item in root.findall(".//item"):
            raw_title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            region = (item.findtext("region") or "Remote").strip()
            # The RSS <description> carries the posting body as HTML; strip tags
            # so it feeds enrichment/scoring as plain text (matches greenhouse).
            desc_html = item.findtext("description") or ""
            desc_text = (
                BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
                if desc_html
                else ""
            )

            # Title format: "Company: Role Title"
            if ": " in raw_title:
                company, role = raw_title.split(": ", 1)
            else:
                company, role = "", raw_title

            if not role or not matches_roles(role, ctx.plugin):
                continue
            if link in seen_urls:
                continue
            if link:
                seen_urls.add(link)

            jobs.append(
                {
                    "company": company,
                    "role": role,
                    "location": region,
                    "url": link,
                    "source": "We Work Remotely",
                    "date_found": today().isoformat(),
                    "description_text": desc_text,
                }
            )

    log.info("[weworkremotely] %d jobs matched", len(jobs))
    return jobs


__all__ = ["scrape_weworkremotely"]
