"""Shared board-health reporting for the board-backed (ATS) sources.

Greenhouse, Ashby and Lever all enumerate one slug per request and collect the
slugs whose fetch failed. They share this module so the degraded rule has one
definition: three copies drift apart the first time one of them is tuned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from daily_driver.core.logging import get_logger

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.context import ScrapeContext

log = get_logger(__name__)


def report_board_failures(
    source_id: str,
    failed_boards: list[str],
    total_boards: int,
    jobs: list[dict],
    ctx: ScrapeContext,
) -> None:
    """Raise PartialSourceError when enough boards failed, else log the rate.

    A board source scrapes hundreds of slugs, and a handful of them are
    permanently gone (404) at any time. Flagging the source degraded for one
    dead board out of 427 reports a 98.5% success rate the same way it reports
    a total outage, which trains the reader to ignore the warning. At or past
    ``discovery.degraded_failure_ratio`` the run still degrades, so a broad
    outage is never mistaken for a clean scrape.

    Row closure is unaffected either way -- see ``closure.decide_closures``.
    """
    from daily_driver.plugins.job_search.scraper.context import PartialSourceError

    if not failed_boards:
        return

    failed = len(failed_boards)
    ok = total_boards - failed
    ratio = failed / total_boards if total_boards else 1.0
    detail = f"{failed} of {total_boards} boards failed: {', '.join(failed_boards)}"

    if ratio < ctx.plugin.discovery.degraded_failure_ratio:
        log.warning(
            "[%s] %d/%d boards ok, %d failed: %s",
            source_id,
            ok,
            total_boards,
            failed,
            ", ".join(failed_boards),
        )
        return

    raise PartialSourceError(jobs, detail)


__all__ = ["report_board_failures"]
