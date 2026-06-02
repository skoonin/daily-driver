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
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


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
    from daily_driver.plugins.job_search.scraper.roles import (
        _search_terms,
        matches_roles,
    )
    from daily_driver.plugins.job_search.scraper.runner import countries_list

    cfg = ctx.plugin.scraper
    roles = list(ctx.plugin.roles)
    terms = _search_terms(ctx.plugin)
    timeout_ms = cfg.timeout * 1000
    max_pages = cfg.max_pages
    jobs: list[dict] = []
    seen_positions: set[str] = set()
    known_urls = ctx.known_urls
    skipped_known = 0
    base_url = "https://jobs.apple.com"

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

            for country in countries_list(ctx.plugin):
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
                        search_input.press("Enter")
                        page.wait_for_timeout(4000)

                        # Scroll to trigger additional API pages
                        for _ in range(max_pages - 1):
                            prev_count = len(api_results)
                            page.evaluate(
                                "window.scrollTo(0, document.body.scrollHeight)"
                            )
                            page.wait_for_timeout(2000)
                            if len(api_results) == prev_count:
                                break
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
                            if not title or not matches_roles(title, roles, ctx.plugin):
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
    except Exception as exc:
        log.warning("[apple] browser session error: %s", exc)

    log.info(
        "[apple] %d jobs matched (%d skipped as already-known)",
        len(jobs),
        skipped_known,
    )
    return jobs


__all__ = ["scrape_apple"]
