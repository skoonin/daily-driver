"""Shared HTTP / Playwright helpers and country lookup tables for scrapers."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, TypeAlias

import requests

log = logging.getLogger(__name__)

# Re-exported so the rest of the scraper layer never imports `requests`
# directly: this module is the single HTTP seam. Callers annotate sessions
# with `Session` and classify per-source failures with `HTTPError` /
# `HTTPTimeout` instead of reaching into `requests`.
Session: TypeAlias = requests.Session
HTTPError = requests.exceptions.RequestException
HTTPTimeout = requests.exceptions.Timeout

_RETRY_STATUS_CODES: frozenset[int] = frozenset({429, 503})
_DEFAULT_BACKOFF_SECONDS: float = 1.5
_MAX_BACKOFF_SECONDS: float = 30.0


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
    from daily_driver.scraper.runner import user_agent

    session = requests.Session()
    session.headers["User-Agent"] = user_agent(config)
    return session


def _retry_after_seconds(resp: requests.Response) -> float | None:
    """Parse Retry-After header (delta-seconds form). Returns None if absent/invalid."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return None


def _api_get(
    session: requests.Session,
    url: str,
    config: dict,
    *,
    label: str = "",
    max_retries: int | None = None,
    sleep: Any = time.sleep,
) -> requests.Response | None:
    """GET a URL with retry on 429/503; log + return None on terminal failure.

    `max_retries` defaults to `scraper.max_retries` from config (3). Honors
    `Retry-After` header when present; otherwise applies exponential backoff
    (1.5s, 3s, 6s, ... capped at 30s). `sleep` is a seam for tests.
    """
    from daily_driver.scraper.runner import max_retries as cfg_max_retries
    from daily_driver.scraper.runner import (
        timeout_seconds,
    )

    timeout = timeout_seconds(config)
    retries = cfg_max_retries(config) if max_retries is None else max_retries
    last_exc: requests.RequestException | None = None

    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= retries:
                break
            sleep(min(_DEFAULT_BACKOFF_SECONDS * (2**attempt), _MAX_BACKOFF_SECONDS))
            continue

        if resp.status_code in _RETRY_STATUS_CODES and attempt < retries:
            wait = _retry_after_seconds(resp)
            if wait is None:
                wait = min(
                    _DEFAULT_BACKOFF_SECONDS * (2**attempt), _MAX_BACKOFF_SECONDS
                )
            log.info(
                "[%s] %s rate-limited (HTTP %d); retrying in %.1fs (attempt %d/%d)",
                label,
                url,
                resp.status_code,
                wait,
                attempt + 1,
                retries,
            )
            sleep(wait)
            continue

        try:
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            break

    if last_exc is not None:
        log.warning("[%s] request failed for %s: %s", label, url, last_exc)
    return None


def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


@contextmanager
def _playwright_browser(config: dict) -> Any:
    """Yield a Playwright Page with non-headless Firefox and realistic settings.

    Non-headless by default -- avoids most bot-detection heuristics on LinkedIn,
    Indeed, and Wellfound without requiring a logged-in session.
    Set job_search.scraper.headless: true in config to run headless.
    """
    from daily_driver.scraper.runner import scraper_cfg, user_agent

    if not _has_playwright():
        raise ImportError(
            "playwright not installed -- reinstall daily-driver and run: playwright install firefox"
        )

    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    headless = scraper_cfg(config).headless
    ua = user_agent(config)

    with sync_playwright() as pw:
        try:
            browser = pw.firefox.launch(headless=headless)
        except (PWError, OSError) as exc:
            log.error(
                "browser launch failed (run: playwright install firefox): %s", exc
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
    "Session",
    "HTTPError",
    "HTTPTimeout",
    "country_params",
    "_http_session",
    "_api_get",
    "_has_playwright",
    "_playwright_browser",
]
