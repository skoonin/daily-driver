"""Per-URL liveness verification for windowed sources (``jobs verify``).

Board-backed sources get closure for free from board-diff (``closure.py``) --
every run re-enumerates their full listings. Windowed sources (search windows,
RSS feeds) never re-show an old posting, so a stored row's liveness has to be
established the direct way: probe its URL with a source-specific closed
detector.

Detector behavior was probed live (2026-07-04) rather than assumed:

- **linkedin**: dead ids 404 cleanly; the guest page serves 200 to a plain UA;
  a closed-but-existing posting carries a "No longer accepting applications"
  banner.
- **apple**: NEVER sniff the details HTML -- it is a JS shell whose string
  table contains every state's marker text on every load. The JSON API
  (``/api/v1/jobDetails/<id>``) 200s for live roles and 404s with a JSON error
  body for dead ones.
- **weworkremotely**: delisted listings 301 to the homepage; live ones 200 in
  place.
- **remoteok**: routes by the numeric id in the slug (301s to the canonical
  slug -- not closure); expired listings still render 200 with a "This job
  post is closed" banner.
- **hn permalinks**: HN items never 404, so a comment URL carries no liveness
  signal at all -- no URL check can ever close those rows.

Rows with no URL to check -- ``verify="none"`` sources (indeed's bot wall,
hn_who_is_hiring's comment permalinks) plus hn_jobs rows whose URL fell back
to an HN permalink -- get the age fallback instead: once their last
affirmative liveness evidence (``Date Verified``, which a scrape re-sighting
still refreshes for every source, else ``Date Found``) is
``unverified_age_days`` old they close as ``age-unverified``. The label is
honest about the weak evidence and the threshold is deliberately generous; a
row re-sighted by a recent run is never age-closed.

``unknown`` (bot-wall, timeout, ambiguous response) NEVER closes -- closure
requires affirmative evidence, and every verification closure (age ones
included) stays reversible: the archive reopen-watch resurfaces false
positives loudly. As a guard against detector rot -- a page redesign that
makes every listing look dead -- a source whose checked rows come back 100%
closed at or above ``_SUSPECT_MIN_CHECKED`` is treated as suspect: its
closures are discarded for the run and reported loudly.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.core.statuses import normalize_status
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.jobs_lock import (
    LOCK_GIVEUP_MESSAGE,
    LOCK_WAIT_TIMEOUT_SECONDS,
    JobsLockTimeout,
    clear_stale_adjacent_lock,
    jobs_lock_path,
    workspace_busy_notice,
)
from daily_driver.plugins.job_search.scraper.context import ScrapeContext
from daily_driver.plugins.job_search.scraper.csv_io import atomic_write_rows, read_rows
from daily_driver.plugins.job_search.scraper.models import (
    BOARD_SOURCE_CANONICALS,
    split_source,
)
from daily_driver.plugins.job_search.scraper.sources import (
    SOURCE_CAPABILITIES,
    SourceCapability,
)
from daily_driver.plugins.job_search.scraper.sources._http import (
    Response,
    Session,
    _api_request,
    _http_session,
)

log = get_logger(__name__)

# Statuses verification may touch: the untriaged inbox, same set board-diff
# closure uses (closure._CLOSABLE_STATUSES). User-triaged rows are the user's
# judgment; verification only annotates rows nobody has judged yet.
_VERIFIABLE_STATUSES = frozenset({"found", "pending"})

# A source whose checked rows come back 100% closed at or above this count is
# suspect (detector rot / site redesign): its closures are discarded this run.
_SUSPECT_MIN_CHECKED = 10

_LINKEDIN_CLOSED_MARKER = "no longer accepting applications"
_REMOTEOK_CLOSED_MARKER = "this job post is closed"

# split_source() canonical head -> SOURCE_CAPABILITIES key, for the sources
# whose Source cell is a display string ("Apple Careers", "HN Who's Hiring")
# rather than the pipeline source id. Board-backed cells already canonicalize
# to their capabilities key. test_verify.py locks this against SCRAPERS.
_SOURCE_CELL_IDS: dict[str, str] = {
    "apple careers": "apple",
    "hn jobs": "hn_jobs",
    "hn who's hiring": "hn_who_is_hiring",
    "we work remotely": "weworkremotely",
    "remoteok": "remoteok",
    "linkedin": "linkedin",
    # Registry-sync completeness only: indeed is verify="none", so selection
    # drops its rows at the capability gate either way.
    "indeed": "indeed",
}


def source_id_for_cell(source_cell: str) -> str | None:
    """Map a jobs.csv Source cell to its SOURCE_CAPABILITIES key, or None."""
    canonical, _board = split_source((source_cell or "").strip())
    if canonical in BOARD_SOURCE_CANONICALS:
        return canonical
    return _SOURCE_CELL_IDS.get(canonical)


@dataclass(frozen=True)
class VerifyOutcome:
    verdict: Literal["live", "closed", "unknown"]
    # Closure evidence grade ("url-404" / "url-marker") or, for unknown, a
    # short human explanation for the summary.
    reason: str = ""


def _live() -> VerifyOutcome:
    return VerifyOutcome("live")


def _closed(reason: str) -> VerifyOutcome:
    return VerifyOutcome("closed", reason)


def _unknown(reason: str) -> VerifyOutcome:
    return VerifyOutcome("unknown", reason)


def _get(url: str, session: Session, ctx: ScrapeContext, label: str) -> Response | None:
    return _api_request(
        session, "GET", url, ctx, label=label, return_error_responses=True
    )


def _check_linkedin(url: str, session: Session, ctx: ScrapeContext) -> VerifyOutcome:
    resp = _get(url, session, ctx, "verify linkedin")
    if resp is None:
        return _unknown("request failed")
    if resp.status_code in (404, 410):
        return _closed("url-404")
    if resp.status_code != 200:
        return _unknown(f"http {resp.status_code}")
    final = (resp.url or "").lower()
    if "authwall" in final or "/login" in final:
        return _unknown("auth wall")
    if _LINKEDIN_CLOSED_MARKER in resp.text.lower():
        return _closed("url-marker")
    return _live()


def _check_apple(url: str, session: Session, ctx: ScrapeContext) -> VerifyOutcome:
    match = re.search(r"/details/(\d+)", url)
    if not match:
        return _unknown("no role id in url")
    api_url = f"https://jobs.apple.com/api/v1/jobDetails/{match.group(1)}?locale=en-us"
    resp = _get(api_url, session, ctx, "verify apple")
    if resp is None:
        return _unknown("request failed")
    if resp.status_code == 200:
        return _live()
    if resp.status_code == 404:
        # Path-rot guard: a dead role 404s with a JSON error body
        # ({"error": "jobsite.general.serviceError"}). An HTML 404 would mean
        # the API endpoint itself moved -- that must never read as closure.
        try:
            payload = resp.json()
        except ValueError:
            return _unknown("non-JSON 404 (API moved?)")
        if isinstance(payload, dict) and "error" in payload:
            return _closed("url-404")
        return _unknown("unrecognized 404 body")
    return _unknown(f"http {resp.status_code}")


def _check_weworkremotely(
    url: str, session: Session, ctx: ScrapeContext
) -> VerifyOutcome:
    resp = _get(url, session, ctx, "verify weworkremotely")
    if resp is None:
        return _unknown("request failed")
    if resp.status_code in (404, 410):
        return _closed("url-404")
    if resp.status_code != 200:
        return _unknown(f"http {resp.status_code}")
    # Delisted jobs 301 to the homepage; requests followed the redirect, so
    # the final URL leaving /remote-jobs/ is the closure signal.
    if not urlparse(resp.url or "").path.startswith("/remote-jobs/"):
        return _closed("url-marker")
    return _live()


def _trailing_listing_id(path: str) -> str:
    match = re.search(r"(\d+)/?$", path)
    return match.group(1) if match else ""


def _check_remoteok(url: str, session: Session, ctx: ScrapeContext) -> VerifyOutcome:
    resp = _get(url, session, ctx, "verify remoteok")
    if resp is None:
        return _unknown("request failed")
    if resp.status_code in (404, 410):
        return _closed("url-404")
    if resp.status_code != 200:
        return _unknown(f"http {resp.status_code}")
    # remoteok routes by the numeric listing id and 301s to the canonical slug
    # (id position in the slug varies by era). A final URL that lost our id
    # means we landed on some other listing -- no verdict about ours.
    requested_id = _trailing_listing_id(urlparse(url).path)
    if requested_id and requested_id not in urlparse(resp.url or "").path:
        return _unknown("redirected to a different listing")
    if _REMOTEOK_CLOSED_MARKER in resp.text.lower():
        return _closed("url-marker")
    return _live()


def _is_hn_permalink(url: str) -> bool:
    return "news.ycombinator.com" in urlparse(url).netloc


def _check_hn(url: str, session: Session, ctx: ScrapeContext) -> VerifyOutcome:
    # hn_jobs falls back to an HN item permalink when a posting has no
    # external URL -- and permalinks never 404, so they say nothing about the
    # job. The age fallback owns those rows once aged; younger ones still
    # land here and read unknown. External URLs get the generic 404 check.
    if _is_hn_permalink(url):
        return _unknown("hn permalink carries no liveness signal")
    resp = _get(url, session, ctx, "verify hn")
    if resp is None:
        return _unknown("request failed")
    if resp.status_code in (404, 410):
        return _closed("url-404")
    if resp.status_code == 200:
        return _live()
    return _unknown(f"http {resp.status_code}")


_URL_CHECKERS: dict[str, Callable[[str, Session, ScrapeContext], VerifyOutcome]] = {
    "linkedin": _check_linkedin,
    "apple": _check_apple,
    "weworkremotely": _check_weworkremotely,
    "remoteok": _check_remoteok,
    "hn_jobs": _check_hn,
}


@dataclass(frozen=True)
class _Target:
    index: int  # row position in jobs.csv
    source_id: str
    url: str
    anchor: date | None  # staleness anchor (Date Verified, else Date Found)


@dataclass
class VerifyReport:
    """Outcome of one verify pass, consumed by the CLI and the manifest."""

    checked: dict[str, int] = field(default_factory=dict)
    live: int = 0
    closed: list[dict[str, str]] = field(default_factory=list)
    unknown: dict[str, int] = field(default_factory=dict)
    suspect_sources: list[str] = field(default_factory=list)
    # Would-be closures a suspect source's circuit breaker discarded -- kept so
    # the "check the site by hand" instruction has a row list to check against.
    discarded_closures: list[dict[str, str]] = field(default_factory=list)
    candidates: int = 0
    interrupted: bool = False
    dry_run: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "checked": dict(self.checked),
            "live": self.live,
            "closed": list(self.closed),
            "unknown": dict(self.unknown),
            "suspect_sources": list(self.suspect_sources),
            "discarded_closures": list(self.discarded_closures),
            "interrupted": self.interrupted,
            "dry_run": self.dry_run,
        }


def _parse_iso(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _untriaged_rows(
    rows: list[dict[str, str]],
    source_filter: frozenset[str] | None,
) -> Iterator[tuple[int, dict[str, str], str, SourceCapability]]:
    """The rows `jobs verify` is allowed to touch, in one place.

    Untriaged status, not already closed, source resolvable to a capability,
    and inside the -S filter. Both selectors (probe targets and age closures)
    build on this so the definition can never drift between them.
    """
    for index, row in enumerate(rows):
        if normalize_status(row.get("Status") or "") not in _VERIFIABLE_STATUSES:
            continue
        if (row.get("Date Closed") or "").strip():
            continue
        source_id = source_id_for_cell(row.get("Source") or "")
        if source_id is None:
            continue
        capability = SOURCE_CAPABILITIES.get(source_id)
        if capability is None:
            continue
        if source_filter and source_id not in source_filter:
            continue
        yield index, row, source_id, capability


def _evidence_anchor(row: dict[str, str]) -> date | None:
    """Last affirmative liveness evidence: Date Verified, else Date Found."""
    return _parse_iso(row.get("Date Verified", "")) or _parse_iso(
        row.get("Date Found", "")
    )


def _select_targets(
    rows: list[dict[str, str]],
    *,
    today: date,
    reverify_days: int,
    source_filter: frozenset[str] | None,
) -> list[_Target]:
    """Pick untriaged url-check rows whose liveness evidence has gone stale."""
    targets: list[_Target] = []
    for index, row, source_id, capability in _untriaged_rows(rows, source_filter):
        if capability.verify != "url-check":
            continue
        url = (row.get("Link") or "").strip()
        if not url:
            continue
        anchor = _evidence_anchor(row)
        # No parseable anchor = never verified; always due.
        if anchor is not None and (today - anchor).days < reverify_days:
            continue
        targets.append(_Target(index, source_id, url, anchor))
    # Stalest evidence first, so a --limit run spends its budget where the
    # record is least trustworthy. None anchors sort first (never verified).
    targets.sort(key=lambda t: (t.anchor is not None, t.anchor or today))
    return targets


def _select_age_closures(
    rows: list[dict[str, str]],
    *,
    today: date,
    unverified_age_days: int,
    source_filter: frozenset[str] | None,
) -> list[tuple[int, str]]:
    """Pick untriaged rows with no URL channel whose liveness evidence aged out.

    Eligible rows: ``verify="none"`` sources, plus url-check sources' rows
    whose URL is an HN permalink (hn_jobs fallback rows -- their checker can
    only ever say unknown). Anchored on ``Date Verified`` else ``Date Found``,
    the same evidence rule as the probe path: a scrape re-sighting refreshes
    ``Date Verified`` for every source (sink._apply_rescan_updates), and a row
    the pipeline saw live days ago must never age-close off its discovery
    date. Rows with no parseable anchor at all are skipped, never closed --
    the opposite of the probe path's "always due", because here selection IS
    the closure decision. Returns ``(row index, source_id)`` pairs.
    """
    picked: list[tuple[int, str]] = []
    for index, row, source_id, capability in _untriaged_rows(rows, source_filter):
        url = (row.get("Link") or "").strip()
        if capability.verify == "none":
            pass
        elif capability.verify == "url-check" and url and _is_hn_permalink(url):
            pass
        else:
            continue
        anchor = _evidence_anchor(row)
        if anchor is None or (today - anchor).days < unverified_age_days:
            continue
        picked.append((index, source_id))
    return picked


def _order_by_host(targets: list[_Target]) -> list[_Target]:
    """Interleave targets across hosts so per-host politeness delays overlap."""
    by_host: dict[str, list[_Target]] = {}
    for target in targets:
        by_host.setdefault(urlparse(target.url).netloc.lower(), []).append(target)
    ordered: list[_Target] = []
    queues = list(by_host.values())
    while queues:
        queues = [queue for queue in queues if queue]
        for queue in queues:
            ordered.append(queue.pop(0))
    return ordered


def _notes_with_closure(notes: str, reason: str, today_iso: str) -> str:
    # Same annotation format as board-diff closure (closure.decide_closures),
    # so prune/reopen flows read both channels identically.
    suffix = f"[closed: {reason} {today_iso}]"
    stripped = (notes or "").strip()
    return f"{stripped} | {suffix}" if stripped else suffix


def _write_verify_manifest(output_dir: Path, payload: dict[str, Any]) -> None:
    path = output_dir / "jobs-last-verify.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        log.warning("Could not write jobs-last-verify.json: %s", exc)


def verify_jobs(
    jobs_csv: Path,
    ephemeral_dir: Path,
    plugin: JobSearchPlugin,
    *,
    reverify_days: int | None = None,
    unverified_age_days: int | None = None,
    sources: frozenset[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    today: date | None = None,
    sleep: Callable[[float], None] = time.sleep,
    session: Session | None = None,
) -> VerifyReport:
    """Probe stale untriaged rows on url-check sources and stamp the outcomes.

    Live rows refresh ``Date Verified``; affirmatively-closed rows get
    ``Status=closed`` + ``Date Closed`` + a Notes annotation and later leave
    for the archive via ``jobs prune``. ``unknown`` never writes anything.
    Rows with no liveness channel at all close as ``age-unverified`` once
    ``Date Found`` is ``unverified_age_days`` old (no probe involved).

    Holds the jobs flock for the whole probe + rewrite: a concurrent scrape
    appending between our read and rewrite would otherwise be silently
    deleted. Ctrl-C mid-probe applies the outcomes gathered so far.
    """
    report = VerifyReport(dry_run=dry_run)
    ctx = ScrapeContext(plugin=plugin)
    days = plugin.verify.reverify_days if reverify_days is None else reverify_days
    age_days = (
        plugin.verify.unverified_age_days
        if unverified_age_days is None
        else unverified_age_days
    )
    today = today or date.today()
    today_iso = today.isoformat()
    started_at = datetime.now(timezone.utc).isoformat()

    clear_stale_adjacent_lock(jobs_csv)
    lock_stack = ExitStack()
    try:
        lock_stack.enter_context(
            file_lock(
                jobs_lock_path(ephemeral_dir),
                timeout=LOCK_WAIT_TIMEOUT_SECONDS,
                on_contention=workspace_busy_notice,
            )
        )
    except TimeoutError:
        raise JobsLockTimeout(LOCK_GIVEUP_MESSAGE) from None
    try:
        header, rows = read_rows(jobs_csv)
        if not header:
            return report

        age_closures = _select_age_closures(
            rows,
            today=today,
            unverified_age_days=age_days,
            source_filter=sources,
        )
        aged_indices = {index for index, _sid in age_closures}
        targets = _select_targets(
            rows, today=today, reverify_days=days, source_filter=sources
        )
        # An hn permalink row due for age closure would only ever probe to
        # unknown; the age channel owns it outright.
        targets = [t for t in targets if t.index not in aged_indices]
        report.candidates = len(targets)
        if limit is not None:
            targets = targets[:limit]
        targets = _order_by_host(targets)

        http = session or _http_session(ctx)
        # Same knob the enrichment detail fetcher paces itself with -- both are
        # polite per-host page fetches against the same hosts.
        delay = plugin.enrichment.detail_delay_seconds
        last_fetch: dict[str, float] = {}
        outcomes: list[tuple[_Target, VerifyOutcome]] = []
        try:
            for target in targets:
                host = urlparse(target.url).netloc.lower()
                elapsed = time.monotonic() - last_fetch.get(host, 0.0)
                if elapsed < delay:
                    sleep(delay - elapsed)
                checker = _URL_CHECKERS[target.source_id]
                outcome = checker(target.url, http, ctx)
                outcomes.append((target, outcome))
                last_fetch[host] = time.monotonic()
                log.debug(
                    "verify %s %s -> %s%s",
                    target.source_id,
                    target.url,
                    outcome.verdict,
                    f" ({outcome.reason})" if outcome.reason else "",
                )
        except KeyboardInterrupt:
            # Keep what we learned: fall through and apply probed outcomes.
            report.interrupted = True
            log.warning(
                "verify interrupted after %d/%d probes; applying gathered outcomes",
                len(outcomes),
                len(targets),
            )

        # Detector-rot circuit breaker: 100% closed across a real sample means
        # the site changed shape under us, not that every job died at once.
        closed_counts: dict[str, int] = {}
        for target, outcome in outcomes:
            report.checked[target.source_id] = (
                report.checked.get(target.source_id, 0) + 1
            )
            if outcome.verdict == "closed":
                closed_counts[target.source_id] = (
                    closed_counts.get(target.source_id, 0) + 1
                )
        for source_id, checked in report.checked.items():
            if (
                checked >= _SUSPECT_MIN_CHECKED
                and closed_counts.get(source_id, 0) == checked
            ):
                report.suspect_sources.append(source_id)
                log.warning(
                    "verify: ALL %d checked %s rows read closed; detector suspect, "
                    "no closures applied for this source",
                    checked,
                    source_id,
                )

        changed = False
        for target, outcome in outcomes:
            row = rows[target.index]
            if outcome.verdict == "live":
                report.live += 1
                if not dry_run:
                    row["Date Verified"] = today_iso
                    changed = True
            elif outcome.verdict == "closed":
                entry = {
                    "company": row.get("Company", ""),
                    "role": row.get("Role", ""),
                    "url": target.url,
                    "source": target.source_id,
                    "reason": outcome.reason,
                }
                if target.source_id in report.suspect_sources:
                    report.unknown["suspect source"] = (
                        report.unknown.get("suspect source", 0) + 1
                    )
                    report.discarded_closures.append(entry)
                    continue
                report.closed.append(entry)
                if not dry_run:
                    row["Status"] = "closed"
                    row["Date Closed"] = today_iso
                    row["Notes"] = _notes_with_closure(
                        row.get("Notes", ""), outcome.reason, today_iso
                    )
                    changed = True
                log.warning(
                    "Verified closed (%s): %s -- %s %s",
                    outcome.reason,
                    row.get("Company", ""),
                    row.get("Role", ""),
                    target.url,
                )
            else:
                report.unknown[outcome.reason] = (
                    report.unknown.get(outcome.reason, 0) + 1
                )

        # Age closures need no probe, no circuit breaker (there is no detector
        # to rot), and always apply -- even after an interrupt, since they were
        # decided from the rows already in hand.
        for index, source_id in age_closures:
            row = rows[index]
            report.closed.append(
                {
                    "company": row.get("Company", ""),
                    "role": row.get("Role", ""),
                    "url": (row.get("Link") or "").strip(),
                    "source": source_id,
                    "reason": "age-unverified",
                }
            )
            if not dry_run:
                row["Status"] = "closed"
                row["Date Closed"] = today_iso
                row["Notes"] = _notes_with_closure(
                    row.get("Notes", ""), "age-unverified", today_iso
                )
                changed = True
            log.warning(
                "Closed age-unverified (found %s, no URL to check): %s -- %s",
                row.get("Date Found", ""),
                row.get("Company", ""),
                row.get("Role", ""),
            )

        if changed:
            atomic_write_rows(jobs_csv, header, rows)
    finally:
        lock_stack.close()

    if not dry_run:
        payload = report.to_payload()
        payload["started_at"] = started_at
        payload["finished_at"] = datetime.now(timezone.utc).isoformat()
        # The knobs that shaped this run, so the manifest is self-describing
        # (candidates vs checked gaps are explainable from the file alone).
        payload["reverify_days"] = days
        payload["unverified_age_days"] = age_days
        payload["limit"] = limit
        payload["sources"] = sorted(sources) if sources else None
        _write_verify_manifest(jobs_csv.parent, payload)
    return report


__all__ = [
    "VerifyOutcome",
    "VerifyReport",
    "source_id_for_cell",
    "verify_jobs",
]
