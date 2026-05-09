"""Wellfound source: Playwright-driven public job search."""

from __future__ import annotations

import logging
import urllib.parse

from daily_driver.core.clock import today
from daily_driver.scraper.sources._http import _playwright_browser

log = logging.getLogger(__name__)


def scrape_wellfound(config: dict) -> list[dict]:
    """Playwright scraper for Wellfound (formerly AngelList Talent) job search.

    Public search results visible without login are limited. Uses domcontentloaded
    plus a fixed wait for React hydration — networkidle never resolves on this SPA.
    CSS class names are hashed so multiple selectors are tried.
    """
    from daily_driver.core.config_models import PlaywrightDelays
    from daily_driver.scraper.runner import (
        _known_urls_from_config,
        _search_terms,
        location_matches,
        matches_roles,
        roles_list,
        scraper_cfg,
        timeout_seconds,
    )

    roles = roles_list(config)
    terms = _search_terms(config)
    timeout_ms = timeout_seconds(config) * 1000

    wf_delays = scraper_cfg(config).playwright_delays.get(
        "wellfound", PlaywrightDelays()
    )
    page_load_ms = wf_delays.page_load_ms
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    known_urls = _known_urls_from_config(config)
    skipped_known = 0

    try:
        with _playwright_browser(config) as page:
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                url = f"https://wellfound.com/jobs?q={encoded}&remote=true"
                try:
                    # networkidle never resolves on Wellfound's SPA — use domcontentloaded
                    # then a fixed wait for React to hydrate the job listings.
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(page_load_ms)
                except Exception as exc:
                    log.warning("[wellfound] navigation failed for %r: %s", term, exc)
                    continue

                # Wellfound uses hashed CSS class names — try multiple selectors
                cards = (
                    page.query_selector_all("[class*='styles_jobListing']")
                    or page.query_selector_all("[data-test='StartupResult']")
                    or page.query_selector_all("div[class*='JobListing']")
                )

                for card in cards:
                    try:
                        title_el = (
                            card.query_selector("[class*='jobTitle']")
                            or card.query_selector("h2")
                            or card.query_selector("h3")
                        )
                        company_el = card.query_selector(
                            "[class*='companyName']"
                        ) or card.query_selector("[class*='startupName']")
                        # Location: Wellfound cards surface location near the role
                        # title with hashed class names containing 'location'.
                        loc_el = (
                            card.query_selector("[class*='location']")
                            or card.query_selector("[class*='Location']")
                            or card.query_selector("[class*='jobLocation']")
                        )
                        # Compensation: salary range displayed as "$X-$Y/yr" in a
                        # span/div whose hashed class contains 'compensation' or 'salary'.
                        comp_el = (
                            card.query_selector("[class*='compensation']")
                            or card.query_selector("[class*='Compensation']")
                            or card.query_selector("[class*='salary']")
                            or card.query_selector("[class*='Salary']")
                        )
                        link_el = card.query_selector("a[href*='/jobs/']")
                        if not title_el:
                            continue
                        role = title_el.inner_text().strip()
                        company = company_el.inner_text().strip() if company_el else ""
                        # Empty string fallback — the pipeline accepts empty location
                        # as potentially remote; callers must not assume any default.
                        location = loc_el.inner_text().strip() if loc_el else ""
                        if not matches_roles(role, roles, config):
                            continue
                        # Filter before queuing detail enrichment to avoid Claude
                        # calls on jobs that will be dropped at the post-scrape stage.
                        if not location_matches({"location": location}, config):
                            continue
                        comp = comp_el.inner_text().strip() if comp_el else ""
                        href = link_el.get_attribute("href") if link_el else ""
                        if href and not href.startswith("http"):
                            href = f"https://wellfound.com{href}"
                        if href in seen_urls:
                            continue
                        if href and href in known_urls:
                            skipped_known += 1
                            continue
                        if href:
                            seen_urls.add(href)
                        job: dict = {
                            "company": company,
                            "role": role,
                            "location": location,
                            "url": href,
                            "source": "Wellfound",
                            "date_found": today().isoformat(),
                        }
                        if comp:
                            job["comp"] = comp
                        jobs.append(job)
                    except Exception as exc:
                        log.debug("[wellfound] parse error on card: %s", exc)
                        continue
    except Exception as exc:
        log.warning("[wellfound] browser session error: %s", exc)

    log.info(
        "[wellfound] %d jobs matched (%d skipped as already-known)",
        len(jobs),
        skipped_known,
    )
    return jobs


__all__ = ["scrape_wellfound"]
