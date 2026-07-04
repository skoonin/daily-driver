"""Apple Careers source: Playwright + intercepted internal JSON API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.countries import (
    APPLE_LOCALE,
    apple_postlocation_code,
    country_names,
)
from daily_driver.plugins.job_search.scraper.sources._http import _playwright_browser

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.context import ScrapeContext

log = get_logger(__name__)

# Bounded fallback for the event-driven /api/v1/search waits. If the SPA never
# fires a matching response within this window the wait degrades to "continue"
# (same outcome as the old fixed sleeps), so a missing response never hangs.
_SEARCH_RESPONSE_TIMEOUT_MS = 5000


def _is_search_response(response: Any) -> bool:
    return "jobs.apple.com/api/v1/search" in response.url


def _await_search_response(page: Any, action: Any) -> bool:
    """Run ``action()`` then wait (event-driven, bounded) for the next
    /api/v1/search response the SPA fires in reaction to it.

    Replaces the old fixed ``wait_for_timeout`` sleeps: the search input's Enter
    and each scroll trigger a POST to /api/v1/search, so waiting on that response
    is both faster (returns the instant data lands) and more reliable than a
    guessed duration. A timeout falls back to the prior behavior — proceed and
    let the result-count stall check decide — so a missing response can't hang.

    Returns True when the wait timed out (fell back), so the caller can count
    fallbacks and surface a single aggregate warning: a site redesign that breaks
    the predicate would otherwise leave Apple a permanently silent "0 found".
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError

    try:
        with page.expect_response(
            _is_search_response, timeout=_SEARCH_RESPONSE_TIMEOUT_MS
        ):
            action()
    except PWTimeoutError:
        log.debug("[apple] no /api/v1/search response within bound; continuing")
        return True
    return False


def scrape_apple(ctx: ScrapeContext) -> list[dict]:
    """Playwright scraper for Apple's careers search via internal JSON API.

    Apple's jobs site is a React SPA. Server-rendered HTML shows generic
    results; the real search only fires when client JS submits a POST to
    /api/v1/search. We drive the search input with Playwright and intercept
    the API response to get structured JSON (title, location, team, date).

    Scrolls down after the initial search to trigger additional API pages
    (the SPA fires more /api/v1/search POSTs on scroll). max_pages controls
    how many scroll passes per search term.

    Iterates each configured country's locale x search term.
    """
    from daily_driver.plugins.job_search.scraper.context import countries_list
    from daily_driver.plugins.job_search.scraper.roles import (
        _search_terms,
        matches_roles,
    )

    cfg = ctx.plugin.scraper
    terms = _search_terms(ctx.plugin)
    timeout_ms = cfg.timeout * 1000
    max_pages = cfg.max_pages
    jobs: list[dict] = []
    seen_positions: set[str] = set()
    known_urls = ctx.known_urls
    skipped_known = 0
    base_url = "https://jobs.apple.com"
    # Count search-response waits and how many fell back to the timeout bound. A
    # site redesign that breaks the /api/v1/search predicate would otherwise make
    # Apple a permanently silent "0 found"; a high fallback ratio surfaces it.
    wait_attempts = 0
    wait_fallbacks = 0

    try:
        with _playwright_browser(ctx) as page:
            # Resolve via the browser page's request API so the refData call
            # reuses the page session and avoids bot detection. A failed lookup
            # (network, bot-block, non-JSON) returns {} so the country is skipped
            # cleanly instead of aborting the whole Apple run.
            def _fetch_json(u: str) -> Any:
                try:
                    return page.request.get(u).json()
                except Exception as exc:  # noqa: BLE001
                    log.debug("[apple] refData request failed for %s: %s", u, exc)
                    return {}

            # Live progress unit: one country (reported at loop-top so a skipped
            # country still advances the bar).
            countries = countries_list(ctx.plugin)
            total = len(countries)
            done = 0
            for country in countries:
                # Graceful-stop checkpoint between locale/country steps: a
                # Ctrl-C/SIGTERM during scraping sets the flag on the main thread,
                # so return the roles accumulated so far rather than starting the
                # next country.
                if ctx.stop_event.is_set():
                    log.info("[apple] stop requested; keeping %d roles", len(jobs))
                    break
                ctx.report(done, total)
                done += 1
                # Pass the full alias list: Apple's API rejects JobSpy's primary
                # abbreviation ("usa", "uk") but resolves the spelled-out name.
                code_pl = apple_postlocation_code(country_names(country), _fetch_json)
                if code_pl is None:
                    log.debug(
                        "[apple] no postLocation code for %s; skipping "
                        "(JobSpy still covers it)",
                        country,
                    )
                    continue

                # Navigate once per country; reuse the page for each term. en-us
                # locale yields English titles; ?location=x-<CODE> scopes country.
                url = f"{base_url}/{APPLE_LOCALE}/search?location=x-{code_pl}"
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                    page.wait_for_timeout(1500)
                except Exception as exc:
                    log.warning(
                        "[apple] navigation failed for %s: %s", APPLE_LOCALE, exc
                    )
                    continue

                for term in terms:
                    # Fresh list each iteration; closure captures the name, not
                    # the value, so each _capture_response writes to its own list.
                    api_results: list[Any] = []

                    def _capture_response(response: Any) -> None:
                        if "jobs.apple.com/api/v1/search" in response.url:
                            try:
                                body = response.json()
                                api_results.extend(
                                    body.get("res", {}).get("searchResults", [])
                                )
                            except Exception as exc:
                                log.debug("[apple] response parse failed: %s", exc)

                    page.on("response", _capture_response)
                    try:
                        search_input = page.query_selector(
                            "input.search-typeahead-input"
                        )
                        if not search_input:
                            log.warning(
                                "[apple] search input not found for %s", APPLE_LOCALE
                            )
                            break

                        search_input.click()
                        search_input.fill("")
                        page.wait_for_timeout(200)
                        search_input.fill(term)
                        page.wait_for_timeout(500)
                        # Enter fires the search POST; wait on its response rather
                        # than a fixed 4s sleep.
                        wait_attempts += 1
                        if _await_search_response(
                            page, lambda: search_input.press("Enter")
                        ):
                            wait_fallbacks += 1

                        # Scroll to trigger additional API pages; each scroll
                        # fires another search POST, so wait on the response
                        # instead of a fixed 2s sleep.
                        for _ in range(max_pages - 1):
                            prev_count = len(api_results)
                            wait_attempts += 1
                            if _await_search_response(
                                page,
                                lambda: page.evaluate(
                                    "window.scrollTo(0, document.body.scrollHeight)"
                                ),
                            ):
                                wait_fallbacks += 1
                            if len(api_results) == prev_count:
                                break
                        else:
                            # Every scroll produced new rows and we stopped at
                            # the page ceiling -- more likely existed. With
                            # max_pages == 1 no scroll happened, so exhaustion
                            # vs truncation is unknowable; don't flag.
                            if max_pages > 1:
                                ctx.record_saturation(
                                    source="apple",
                                    query=f"{term} x {country}",
                                    returned=len(api_results),
                                    requested=len(api_results),
                                    kind="cap",
                                )
                    except Exception as exc:
                        log.warning(
                            "[apple] search failed for %r (%s): %s", term, country, exc
                        )
                        continue
                    finally:
                        page.remove_listener("response", _capture_response)

                    for item in api_results:
                        try:
                            title = item.get("postingTitle", "")
                            if not title or not matches_roles(title, ctx.plugin):
                                continue
                            position_id = item.get("positionId", "") or item.get(
                                "id", ""
                            )
                            if not position_id:
                                continue
                            if position_id in seen_positions:
                                continue
                            seen_positions.add(position_id)
                            locs = item.get("locations", [])
                            # Keep countryName: a bare city ("Seattle") matches no
                            # country alias in location_matches(), so the country
                            # branch would drop every Apple job.
                            if locs:
                                first = locs[0]
                                parts = [
                                    first.get(field, "")
                                    for field in (
                                        "name",
                                        "stateProvince",
                                        "countryName",
                                    )
                                ]
                                location = ", ".join(p for p in parts if p) or "Various"
                            else:
                                location = "Various"
                            job_id = item.get("id", position_id)
                            slug = item.get("transformedPostingTitle", "")
                            detail_url = (
                                f"{base_url}/{APPLE_LOCALE}/details/{job_id}/{slug}"
                            )
                            if detail_url in known_urls:
                                skipped_known += 1
                                continue
                            jobs.append(
                                {
                                    "company": "Apple",
                                    "role": title,
                                    "location": location,
                                    "url": detail_url,
                                    "source": "Apple Careers",
                                    "date_found": today().isoformat(),
                                    # Per-search ISO country: the origin-country
                                    # hint the row lift uses when the location
                                    # text names no country.
                                    "origin_country": country,
                                }
                            )
                        except Exception as exc:
                            log.debug("[apple] parse error on result: %s", exc)
                            continue

                    log.debug(
                        "[apple] %s/%s: %d API results, %d matched",
                        APPLE_LOCALE,
                        term,
                        len(api_results),
                        len(jobs),
                    )

                # Per-country heartbeat at INFO so -v shows motion through the
                # long (multi-minute, multi-country) Apple scrape.
                log.info("[apple] %s: %d roles matched so far", country, len(jobs))
    except KeyboardInterrupt:
        # First Ctrl-C/SIGTERM lands here on the coordinator thread mid-country.
        # Flag the graceful stop so the orchestrator marks the run interrupted,
        # and KEEP the roles accumulated so far instead of dropping them.
        ctx.stop_event.set()
        log.info("[apple] interrupted; keeping %d roles found so far", len(jobs))
    except Exception as exc:
        log.warning("[apple] browser session error: %s", exc)

    # One aggregate warning when search-response waits fell back to the timeout
    # bound: persistent fallbacks mean the SPA never fired the intercepted
    # /api/v1/search POST (likely a site change), which would otherwise hide
    # behind a silent "0 found" success.
    if wait_fallbacks:
        log.warning(
            "[apple] search-response wait timed out on %d/%d attempts — "
            "site may have changed (results may be incomplete)",
            wait_fallbacks,
            wait_attempts,
        )

    log.info(
        "[apple] %d jobs matched (%d skipped as already-known)",
        len(jobs),
        skipped_known,
    )
    return jobs


__all__ = ["scrape_apple"]
