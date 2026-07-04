"""Greenhouse source: public Job Board API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


def scrape_greenhouse(ctx: ScrapeContext) -> list[dict]:
    """Scrape jobs from the Greenhouse Job Board API (public, no auth required).

    Reads board slugs from config at
    job_search.sources.greenhouse.greenhouse_boards (default: ["anthropic"]).
    Each slug maps to
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    which returns all jobs with full HTML descriptions in a single request.
    """
    from daily_driver.plugins.job_search.config import GreenhouseToggle
    from daily_driver.plugins.job_search.scraper.roles import matches_roles
    from daily_driver.plugins.job_search.scraper.runner import (
        PartialSourceError,
        source_toggle,
    )

    boards = source_toggle(ctx.plugin, "greenhouse", GreenhouseToggle).greenhouse_boards
    session = _http_session(ctx)
    jobs: list[dict] = []
    # Boards whose fetch failed -> the source is degraded (its result is
    # incomplete). A transient board 404 must never look identical to a clean
    # scrape: board-diff closure would read the missing board's rows as
    # "absent = closed" while the source reported healthy.
    failed_boards: list[str] = []

    # Live progress unit: one board (reported at loop-top so a skipped board
    # still advances the bar).
    total = len(boards)
    done = 0
    for board in boards:
        # Graceful-stop checkpoint between boards: return what is matched so far.
        if ctx.stop_event.is_set():
            log.info("[greenhouse] stop requested; keeping %d jobs so far", len(jobs))
            return jobs
        ctx.report(done, total)
        done += 1
        api_url = (
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        )
        resp = _api_get(session, api_url, ctx, label=f"greenhouse/{board}")
        if not resp:
            failed_boards.append(board)
            continue
        data = resp.json()

        board_jobs = data.get("jobs", [])
        company_name = board.replace("-", " ").title()
        # Use the first job's metadata to get the real company name if available.
        if board_jobs:
            first_meta = board_jobs[0].get("company_name")
            if first_meta:
                company_name = first_meta

        for entry in board_jobs:
            title = entry.get("title", "")
            if not title or not matches_roles(title, ctx.plugin):
                continue

            loc = entry.get("location", {})
            location = loc.get("name", "") if isinstance(loc, dict) else str(loc)

            absolute_url = entry.get("absolute_url", "")
            desc_html = entry.get("content", "")
            desc_text = ""
            if desc_html:
                desc_text = BeautifulSoup(desc_html, "html.parser").get_text(
                    " ", strip=True
                )

            jobs.append(
                {
                    "company": company_name,
                    "role": title,
                    "location": location or "Remote",
                    "url": absolute_url,
                    "source": f"Greenhouse ({board})",
                    "date_found": today().isoformat(),
                    "description_text": desc_text,
                }
            )

        log.info(
            "[greenhouse] %s: %d jobs matched out of %d listed",
            board,
            sum(1 for j in jobs if j["source"] == f"Greenhouse ({board})"),
            len(board_jobs),
        )

    if failed_boards:
        # Incomplete scrape (one or more boards failed) -> degraded, not a
        # clean run. The gathered jobs ride along and still append.
        raise PartialSourceError(
            jobs,
            f"{len(failed_boards)} of {len(boards)} boards failed: "
            f"{', '.join(failed_boards)}",
        )
    return jobs


__all__ = ["scrape_greenhouse"]
