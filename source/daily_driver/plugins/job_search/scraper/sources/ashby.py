"""AshbyHQ source: public Job Posting API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


def scrape_ashby(ctx: ScrapeContext) -> list[dict]:
    """Scrape jobs from the AshbyHQ Job Posting API (public, no auth required).

    Scrapes the union of the hand-pinned config boards
    (job_search.sources.ashby.ashby_boards, default []) and the boards the
    `jobs discover-boards` sweep matched (ctx.discovered_boards), minus the
    exclude_boards blocklist. Each slug maps to
    https://api.ashbyhq.com/posting-api/job-board/{slug} which returns all
    listed postings with plain-text descriptions in a single request.

    Unlike Greenhouse, the Ashby posting API carries no per-job company field
    (top-level keys are only ``jobs`` and ``apiVersion``), so the company name
    is derived from the board slug.
    """
    from daily_driver.plugins.job_search.config import AshbyToggle
    from daily_driver.plugins.job_search.scraper.discovery import resolve_boards
    from daily_driver.plugins.job_search.scraper.roles import matches_roles
    from daily_driver.plugins.job_search.scraper.runner import (
        PartialSourceError,
        source_toggle,
    )

    toggle = source_toggle(ctx.plugin, "ashby", AshbyToggle)
    boards = resolve_boards(
        toggle.ashby_boards,
        ctx.discovered_boards.get("ashby", ()),
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
            log.info("[ashby] stop requested; keeping %d jobs so far", len(jobs))
            return jobs
        ctx.report(done, total)
        done += 1
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        resp = _api_get(session, api_url, ctx, label=f"ashby/{board}")
        if not resp:
            failed_boards.append(board)
            continue
        data = resp.json()

        board_jobs = data.get("jobs", [])
        # Raw listing pre role-filter for board-diff closure. De-listed
        # postings (isListed False) are NOT publicly present, so they count as
        # absent -- exactly the signal closure needs.
        ctx.record_enumeration(
            f"Ashby ({board})",
            {
                entry.get("jobUrl", "")
                for entry in board_jobs
                if entry.get("isListed") is not False
            },
        )
        # No per-job company field in the Ashby response; derive from the slug.
        company_name = board.replace("-", " ").title()

        for entry in board_jobs:
            # Ashby still returns de-listed postings; skip them explicitly.
            if entry.get("isListed") is False:
                continue
            title = entry.get("title", "")
            if not title or not matches_roles(title, ctx.plugin):
                continue

            jobs.append(
                {
                    "company": company_name,
                    "role": title,
                    # Ashby `location` is a plain string (Greenhouse nests it).
                    "location": entry.get("location", "") or "Remote",
                    "url": entry.get("jobUrl", ""),
                    "source": f"Ashby ({board})",
                    "date_found": today().isoformat(),
                    # Ashby ships plain text directly; no HTML stripping needed.
                    "description_text": entry.get("descriptionPlain", ""),
                }
            )

        log.info(
            "[ashby] %s: %d jobs matched out of %d returned",
            board,
            sum(1 for j in jobs if j["source"] == f"Ashby ({board})"),
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


__all__ = ["scrape_ashby"]
