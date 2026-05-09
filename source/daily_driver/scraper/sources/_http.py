"""Shared HTTP / Playwright helpers and country lookup tables for scrapers."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

import requests

log = logging.getLogger(__name__)


# Per-scraper parameters for each supported country. Add a row here when
# introducing a new country code in .dd-config.yaml.
#
# Keys:
#   apple_locale          - path segment for jobs.apple.com/{locale}/search
#   linkedin_location     - value for LinkedIn's ?location= query param
#   indeed_host           - hostname for Indeed's regional site
COUNTRY_MAP: dict[str, dict[str, str]] = {
    "US": {
        "apple_locale": "en-us",
        "linkedin_location": "United States",
        "indeed_host": "www.indeed.com",
    },
    "CA": {
        "apple_locale": "en-ca",
        "linkedin_location": "Canada",
        "indeed_host": "ca.indeed.com",
    },
    "GB": {
        "apple_locale": "en-gb",
        "linkedin_location": "United Kingdom",
        "indeed_host": "uk.indeed.com",
    },
    "IE": {
        "apple_locale": "en-ie",
        "linkedin_location": "Ireland",
        "indeed_host": "ie.indeed.com",
    },
    "AU": {
        "apple_locale": "en-au",
        "linkedin_location": "Australia",
        "indeed_host": "au.indeed.com",
    },
    "ZA": {
        "apple_locale": "en-za",
        "linkedin_location": "South Africa",
        "indeed_host": "za.indeed.com",
    },
}

# Country display names and common aliases for location matching.
# Used by location_matches() to check if a job's location text belongs to
# an allowed country. ISO codes are excluded to avoid false positives
# (e.g., "CA" matching California instead of Canada).
COUNTRY_NAMES: dict[str, list[str]] = {
    "US": ["United States", "USA"],
    "CA": ["Canada"],
    "GB": ["United Kingdom", "UK", "England", "Scotland", "Wales"],
    "IE": ["Ireland"],
    "AU": ["Australia"],
    "ZA": ["South Africa"],
}


def country_params(country: str) -> dict[str, str]:
    """Look up per-scraper parameters for an ISO country code.

    Returns {} and logs a warning for unknown codes so the caller can skip
    that country without crashing the run.
    """
    params = COUNTRY_MAP.get(country.upper())
    if params is None:
        log.warning("[country] unknown country code %r, skipping", country)
        return {}
    return params


def _http_session(config: dict) -> requests.Session:
    """Build a reusable requests.Session from scraper config."""
    from daily_driver.scraper._impl import user_agent

    session = requests.Session()
    session.headers["User-Agent"] = user_agent(config)
    return session


def _api_get(
    session: requests.Session,
    url: str,
    config: dict,
    *,
    label: str = "",
) -> requests.Response | None:
    """GET a URL, log + return None on failure instead of raising."""
    from daily_driver.scraper._impl import timeout_seconds

    try:
        resp = session.get(url, timeout=timeout_seconds(config))
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        log.warning("[%s] request failed for %s: %s", label, url, exc)
        return None


def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


@contextmanager
def _playwright_browser(config: dict) -> Any:
    """Yield a Playwright Page with non-headless Chromium and realistic settings.

    Non-headless by default — avoids most bot-detection heuristics on LinkedIn,
    Indeed, and Wellfound without requiring a logged-in session.
    Set job_search.scraper.headless: true in config to run headless.
    """
    from daily_driver.scraper._impl import scraper_cfg, user_agent

    if not _has_playwright():
        raise ImportError(
            "playwright not installed — run: pip install playwright && playwright install chromium"
        )

    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    headless = scraper_cfg(config).headless
    ua = user_agent(config)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=headless)
        except (PWError, OSError) as exc:
            log.error(
                "browser launch failed (run: playwright install chromium): %s", exc
            )
            raise
        ctx = browser.new_context(
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = ctx.new_page()
        try:
            yield page
        finally:
            ctx.close()
            browser.close()


__all__ = [
    "COUNTRY_MAP",
    "COUNTRY_NAMES",
    "country_params",
    "_http_session",
    "_api_get",
    "_has_playwright",
    "_playwright_browser",
]
