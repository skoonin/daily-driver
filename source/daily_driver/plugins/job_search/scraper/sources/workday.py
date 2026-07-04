"""Workday source: public CXS job-search API (paginated POST)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_post,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.context import ScrapeContext

log = get_logger(__name__)

# Workday returns a fixed page size; 20 is the size its own career sites request.
_PAGE_SIZE = 20
# Hard ceiling on pages per board so a misreported `total` (or an endpoint that
# keeps returning postings) cannot spin the fetch loop forever.
_MAX_PAGES = 200


def scrape_workday(ctx: ScrapeContext) -> list[dict]:
    """Scrape jobs from a Workday careers site's public CXS API (no auth).

    Reads board identifiers from config at
    job_search.sources.workday.workday_boards (default: []). Each board names
    the three parts of a Workday URL — ``tenant``, ``host``, ``site`` — which
    map to the search endpoint
    ``https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs``.

    Unlike the single-GET board sources, Workday paginates: the endpoint is a
    POST taking ``{limit, offset}`` and returns ``{total, jobPostings[]}``, so
    each board is walked offset by offset until ``total`` is reached. The
    postings carry no company field (the tenant is the company) and no
    description, so the company is taken from the board's ``company`` override
    or derived from the tenant, and ``description_text`` is emitted empty.
    """
    from daily_driver.plugins.job_search.config import WorkdayToggle
    from daily_driver.plugins.job_search.scraper.context import (
        CheckpointAborted,
        PartialSourceError,
        source_toggle,
    )
    from daily_driver.plugins.job_search.scraper.roles import matches_roles

    boards = source_toggle(ctx.plugin, "workday", WorkdayToggle).workday_boards
    session = _http_session(ctx)
    jobs: list[dict] = []
    # Boards whose pagination broke off on an HTTP failure -> the source is
    # degraded (its result is incomplete), surfaced after the loop.
    degraded_boards: list[str] = []

    # Live progress unit: one board (reported at loop-top so a skipped board
    # still advances the bar). Pagination happens within a board.
    total_boards = len(boards)
    done = 0
    for board in boards:
        # Graceful-stop checkpoint between boards: return what is matched so far.
        if ctx.stop_event.is_set():
            log.info("[workday] stop requested; keeping %d jobs so far", len(jobs))
            return jobs
        ctx.report(done, total_boards)
        done += 1

        base = f"https://{board.tenant}.{board.host}.myworkdayjobs.com"
        api_url = f"{base}/wday/cxs/{board.tenant}/{board.site}/jobs"
        company_name = board.company or board.tenant.replace("-", " ").title()
        label = f"workday/{board.tenant}"
        matched_before = len(jobs)

        offset = 0
        seen = 0
        total: int | None = None
        partial = False
        ceiling = False
        # Raw listing pre role-filter, for board-diff closure. Recorded only
        # when the board's pagination completed cleanly -- a partial or
        # page-capped walk is an incomplete inventory and must not close rows.
        board_urls: set[str] = set()
        for page in range(_MAX_PAGES):
            # Interrupt promptly even mid-board on a large, many-page tenant.
            # The in-flight board's fetched rows are kept (partial rows are
            # kept by design), so checkpoint them before returning -- a
            # checkpointed source's end-of-source append is skipped, and
            # without this the graceful stop would silently drop them.
            if ctx.stop_event.is_set():
                log.info("[workday] stop requested; keeping %d jobs so far", len(jobs))
                board_new = jobs[matched_before:]
                if board_new:
                    try:
                        ctx.checkpoint(board_new)
                    except CheckpointAborted:
                        pass  # already stopping; rows were kept in memory only
                return jobs
            body = {
                "limit": _PAGE_SIZE,
                "offset": offset,
                "searchText": "",
                "appliedFacets": {},
            }
            resp = _api_post(session, api_url, ctx, json=body, label=label)
            if not resp:
                # Mid-pagination HTTP failure: keep what we have, but flag it.
                # A paginated board can otherwise return a partial set that
                # looks identical to a complete, healthy scrape.
                partial = True
                break
            data = resp.json()
            if page == 0:
                # `total` is fixed for the board; read it once. Treat its
                # absence as a malformed shape, not a "stop now" sentinel —
                # a defaulted 0 would silently cap every board at page 1.
                total = data.get("total")
                if total is None:
                    log.warning(
                        "[workday] %s: response missing 'total'; paginating"
                        " until an empty page",
                        board.tenant,
                    )
            postings = data.get("jobPostings", [])
            if not postings:
                break
            seen += len(postings)

            for entry in postings:
                # externalPath is site-relative; the public posting lives under
                # the localized careers path. Built before the role filter so
                # the enumeration covers the board's whole inventory.
                path = entry.get("externalPath", "")
                url = f"{base}/en-US/{board.site}{path}" if path else ""
                if url:
                    board_urls.add(url)
                title = entry.get("title", "")
                if not title or not matches_roles(title, ctx.plugin):
                    continue
                jobs.append(
                    {
                        "company": company_name,
                        "role": title,
                        "location": entry.get("locationsText", "") or "Remote",
                        "url": url,
                        "source": f"Workday ({board.tenant})",
                        "date_found": today().isoformat(),
                        # CXS list payload carries no per-job description text.
                        "description_text": "",
                    }
                )

            # Advance by what the page actually returned, not the requested
            # _PAGE_SIZE: a short non-final page would otherwise skip the gap
            # between len(postings) and _PAGE_SIZE and miss those postings.
            offset += len(postings)
            if total is not None and offset >= total:
                break
        else:
            ceiling = True
            log.warning(
                "[workday] %s: hit the %d-page ceiling; some postings may be"
                " unfetched",
                board.tenant,
                _MAX_PAGES,
            )
            ctx.record_saturation(
                source="workday",
                query=board.tenant,
                returned=seen,
                # Unknown board total (missing 'total' field): report the
                # ceiling's capacity so returned/requested still reads as
                # truncated, never as an exact seen/seen "complete" match.
                requested=(total if total is not None else _MAX_PAGES * _PAGE_SIZE),
                kind="cap",
            )

        if not partial and not ceiling:
            ctx.record_enumeration(f"Workday ({board.tenant})", board_urls)

        matched = len(jobs) - matched_before
        if partial:
            # Loud, not green: this board's result is incomplete by HTTP failure.
            degraded_boards.append(board.tenant)
            log.warning(
                "[workday] %s: fetch failed at offset %d; %d matched from %d"
                " postings seen (PARTIAL board)",
                board.tenant,
                offset,
                matched,
                seen,
            )
        else:
            # Keep the "out of N returned" denominator: it distinguishes
            # "fetched N, matched 0" from a renamed field returning nothing.
            log.info(
                "[workday] %s: %d matched out of %d returned",
                board.tenant,
                matched,
                seen,
            )

        # Per-board durable checkpoint (pattern: scrape_jobspy); a partial
        # board's rows are kept by design, so they checkpoint too. On a persist
        # failure stop AT this board rather than fetch on against a dead disk.
        board_new = jobs[matched_before:]
        if board_new:
            try:
                ctx.checkpoint(board_new)
            except CheckpointAborted:
                log.warning(
                    "[workday] checkpoint persist failed; stopping after %d jobs",
                    len(jobs),
                )
                return jobs

    if degraded_boards:
        # Signal the orchestrator this scrape is incomplete (kept jobs, but some
        # boards lost pagination mid-walk) so it reads as degraded, not a clean
        # run. The gathered jobs ride along on the exception and still append.
        raise PartialSourceError(
            jobs,
            f"{len(degraded_boards)} of {len(boards)} boards returned incomplete "
            f"results: {', '.join(degraded_boards)}",
        )
    return jobs


__all__ = ["scrape_workday"]
