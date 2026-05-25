"""Greenhouse source: public Job Board API."""

from __future__ import annotations

from bs4 import BeautifulSoup

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

log = get_logger(__name__)


def scrape_greenhouse(config: dict) -> list[dict]:
    """Scrape jobs from the Greenhouse Job Board API (public, no auth required).

    Reads board slugs from config at
    job_search.sources.greenhouse.greenhouse_boards (default: ["anthropic"]).
    Each slug maps to
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    which returns all jobs with full HTML descriptions in a single request.
    """
    from daily_driver.plugins.job_search.config import GreenhouseToggle
    from daily_driver.plugins.job_search.scraper.runner import (
        matches_roles,
        roles_list,
        source_toggle,
    )

    roles = roles_list(config)
    boards = source_toggle(config, "greenhouse", GreenhouseToggle).greenhouse_boards
    session = _http_session(config)
    jobs: list[dict] = []

    for board in boards:
        api_url = (
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        )
        resp = _api_get(session, api_url, config, label=f"greenhouse/{board}")
        if not resp:
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
            if not title or not matches_roles(title, roles, config):
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

    return jobs


__all__ = ["scrape_greenhouse"]
