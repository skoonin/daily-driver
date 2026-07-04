"""Shared HTTP / Playwright helpers for scrapers."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeAlias

import requests

from daily_driver.core.logging import get_logger

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)

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


def _http_session(ctx: ScrapeContext) -> requests.Session:
    """Build a reusable requests.Session from scraper config."""
    session = requests.Session()
    session.headers["User-Agent"] = ctx.plugin.scraper.user_agent
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


def _api_request(
    session: requests.Session,
    method: str,
    url: str,
    ctx: ScrapeContext,
    *,
    json: Any = None,
    label: str = "",
    max_retries: int | None = None,
    sleep: Any = time.sleep,
    headers: dict[str, str] | None = None,
    return_error_responses: bool = False,
) -> requests.Response | None:
    """Issue an HTTP request with retry on 429/503; log + return None on failure.

    Shared by `_api_get` / `_api_post`. `max_retries` defaults to
    `scraper.max_retries` from config (3). Honors `Retry-After` when present;
    otherwise applies exponential backoff (1.5s, 3s, 6s, ... capped at 30s).
    `json`, when given, is sent as the request body. `headers`, when given,
    are merged onto the session headers for this request only (e.g. the
    browser-like set a login-free LinkedIn page expects). `sleep` is a seam for
    tests.

    With ``return_error_responses`` the final response comes back even on a
    non-2xx status (transport failures still return None): callers that need
    the status code itself — board discovery caching a 404/410 slug as
    permanently dead, which must never be conflated with a timeout — read it
    off the response instead of losing it to the None collapse.
    """
    timeout = ctx.plugin.scraper.timeout
    retries = ctx.plugin.scraper.max_retries if max_retries is None else max_retries
    last_exc: requests.RequestException | None = None

    for attempt in range(retries + 1):
        try:
            if method == "POST":
                resp = session.post(url, json=json, timeout=timeout, headers=headers)
            else:
                resp = session.get(url, timeout=timeout, headers=headers)
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
            if return_error_responses:
                return resp
            last_exc = exc
            break

    if last_exc is not None:
        log.warning("[%s] request failed for %s: %s", label, url, last_exc)
    return None


def _api_get(
    session: requests.Session,
    url: str,
    ctx: ScrapeContext,
    *,
    label: str = "",
    max_retries: int | None = None,
    sleep: Any = time.sleep,
    headers: dict[str, str] | None = None,
) -> requests.Response | None:
    """GET a URL with retry on 429/503; log + return None on terminal failure."""
    return _api_request(
        session,
        "GET",
        url,
        ctx,
        label=label,
        max_retries=max_retries,
        sleep=sleep,
        headers=headers,
    )


def _api_post(
    session: requests.Session,
    url: str,
    ctx: ScrapeContext,
    *,
    json: Any,
    label: str = "",
    max_retries: int | None = None,
    sleep: Any = time.sleep,
) -> requests.Response | None:
    """POST a JSON body with retry on 429/503; log + return None on failure."""
    return _api_request(
        session,
        "POST",
        url,
        ctx,
        json=json,
        label=label,
        max_retries=max_retries,
        sleep=sleep,
    )


def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


@contextmanager
def _playwright_browser(ctx: ScrapeContext) -> Any:
    """Yield a Playwright Page with a realistic, non-headless browser context.

    Non-headless by default -- avoids most bot-detection heuristics on LinkedIn,
    Indeed, and Wellfound without requiring a logged-in session.
    Set plugins.job_search.scraper.headless: true in config to run headless.
    The engine is selectable via plugins.job_search.scraper.browser (firefox
    default).
    """
    engine = ctx.plugin.scraper.browser
    if not _has_playwright():
        raise ImportError(
            f"playwright not installed -- reinstall daily-driver and run: "
            f"playwright install {engine}"
        )

    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    headless = ctx.plugin.scraper.headless
    ua = ctx.plugin.scraper.user_agent

    with sync_playwright() as pw:
        try:
            browser = getattr(pw, engine).launch(headless=headless)
        except (PWError, OSError) as exc:
            log.error(
                "browser launch failed (run: playwright install %s): %s", engine, exc
            )
            raise
        browser_ctx = browser.new_context(
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = browser_ctx.new_page()
        try:
            yield page
        finally:
            browser_ctx.close()
            browser.close()


__all__ = [
    "Session",
    "HTTPError",
    "HTTPTimeout",
    "_http_session",
    "_api_request",
    "_api_get",
    "_api_post",
    "_has_playwright",
    "_playwright_browser",
]
