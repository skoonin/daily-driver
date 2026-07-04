"""Lever source: public Postings API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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


def _description_text(entry: dict[str, Any]) -> str:
    """Full plain-text description for one posting.

    ``descriptionPlain`` carries only the opening + body (verified live: it
    equals ``openingPlain`` + ``descriptionBodyPlain``); the requirements /
    qualifications live in ``lists`` (per-section HTML ``content``) and any
    closing blurb in ``additionalPlain``. Fit scoring needs the lists most,
    so compose all three.
    """
    parts = [(entry.get("descriptionPlain") or "").strip()]
    for section in entry.get("lists") or []:
        if not isinstance(section, dict):
            continue
        heading = (section.get("text") or "").strip()
        content_html = section.get("content") or ""
        content = (
            BeautifulSoup(content_html, "html.parser").get_text(" ", strip=True)
            if content_html
            else ""
        )
        if heading or content:
            parts.append(f"{heading}\n{content}".strip())
    parts.append((entry.get("additionalPlain") or "").strip())
    return "\n\n".join(part for part in parts if part)


def _location(entry: dict[str, Any]) -> str:
    """Location string with the remote signal made explicit.

    ``categories.location`` often names only a country/city (e.g. "US") while
    ``workplaceType: remote`` carries the remote fact. The location filter is
    the only filter that drops jobs, so surface that fact in the string a
    city-map country entry would otherwise drop.
    """
    categories = entry.get("categories") or {}
    location = (categories.get("location") or "").strip()
    if not location:
        return "Remote"
    if entry.get("workplaceType") == "remote" and "remote" not in location.lower():
        return f"{location} (Remote)"
    return location


def scrape_lever(ctx: ScrapeContext) -> list[dict]:
    """Scrape jobs from the Lever Postings API (public, no auth required).

    Scrapes the union of the hand-pinned config boards
    (job_search.sources.lever.lever_boards, default []) and the boards the
    `jobs discover-boards` sweep matched (ctx.discovered_boards), minus the
    exclude_boards blocklist. Each slug maps to
    https://api.lever.co/v0/postings/{slug}?mode=json which returns ALL
    listed postings as a single JSON array (no wrapper object); an empty
    board returns ``[]``, a dead slug HTTP 404.

    Like Ashby, the payload carries no company field, so the company name is
    derived from the board slug.
    """
    from daily_driver.plugins.job_search.config import LeverToggle
    from daily_driver.plugins.job_search.scraper.discovery import resolve_boards
    from daily_driver.plugins.job_search.scraper.roles import matches_roles
    from daily_driver.plugins.job_search.scraper.runner import (
        PartialSourceError,
        source_toggle,
    )

    toggle = source_toggle(ctx.plugin, "lever", LeverToggle)
    boards = resolve_boards(
        toggle.lever_boards,
        ctx.discovered_boards.get("lever", ()),
        toggle.exclude_boards,
    )
    session = _http_session(ctx)
    jobs: list[dict] = []
    # Boards whose request failed -> the source is degraded (its result is
    # incomplete). An all-failed source returns [] otherwise indistinguishable
    # from a clean "no roles matched"; surfaced after the loop.
    failed_boards: list[str] = []

    # Live progress unit: one board (reported at loop-top so a skipped board
    # still advances the bar).
    total = len(boards)
    done = 0
    for board in boards:
        # Graceful-stop checkpoint between boards: return what is matched so far.
        if ctx.stop_event.is_set():
            log.info("[lever] stop requested; keeping %d jobs so far", len(jobs))
            return jobs
        ctx.report(done, total)
        done += 1
        api_url = f"https://api.lever.co/v0/postings/{board}?mode=json"
        resp = _api_get(session, api_url, ctx, label=f"lever/{board}")
        if not resp:
            failed_boards.append(board)
            continue
        board_jobs = resp.json()
        if not isinstance(board_jobs, list):
            # The endpoint contract is a bare array; anything else is a broken
            # fetch, not a valid (possibly empty) enumeration.
            failed_boards.append(board)
            continue

        # Raw listing pre role-filter for board-diff closure. An empty array is
        # a valid enumeration: a live board with nothing open.
        ctx.record_enumeration(
            f"Lever ({board})",
            {entry.get("hostedUrl", "") for entry in board_jobs},
        )
        # No company field in the Lever payload; derive from the slug.
        company_name = board.replace("-", " ").title()

        for entry in board_jobs:
            title = entry.get("text", "")
            if not title or not matches_roles(title, ctx.plugin):
                continue

            jobs.append(
                {
                    "company": company_name,
                    "role": title,
                    "location": _location(entry),
                    "url": entry.get("hostedUrl", ""),
                    "source": f"Lever ({board})",
                    "date_found": today().isoformat(),
                    "description_text": _description_text(entry),
                }
            )

        log.info(
            "[lever] %s: %d jobs matched out of %d returned",
            board,
            sum(1 for j in jobs if j["source"] == f"Lever ({board})"),
            len(board_jobs),
        )

    if failed_boards:
        # Incomplete scrape (one or more boards failed) -> degraded, not a clean
        # run. The gathered jobs ride along and still append.
        raise PartialSourceError(
            jobs,
            f"{len(failed_boards)} of {len(boards)} boards failed: "
            f"{', '.join(failed_boards)}",
        )
    return jobs


__all__ = ["scrape_lever"]
