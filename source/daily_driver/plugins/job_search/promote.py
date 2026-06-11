"""Promote a jobs.csv row into a tracker `job` entry.

The tracker is the system that *drives* work (follow-ups, day-start planning);
jobs.csv is the discovery / triage record. A row only earns a tracker entry once
it needs active driving (interviews, follow-ups), so promotion is an explicit
bridge the user invokes — never automatic.

Promotion is single-purpose: it creates the tracker entry and does NOT mutate
the jobs.csv row (the user owns the row's Status). The job URL is the durable
key linking the two, so promoting the same URL twice is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from daily_driver.core.statuses import normalize_status
from daily_driver.core.tracker import JOB_RECOMMENDED_STATUSES, Tracker, TrackerEntry
from daily_driver.plugins.job_search.scraper.csv_io import read_rows

# A row promoted because it needs active driving but carrying no status (or one
# outside the job lifecycle) lands here. `applied` is the earliest active state:
# promotion means the user is acting on the row, past pure triage.
_FALLBACK_STATUS = "applied"

_JOB_STATUS_SET = {normalize_status(s) for s in JOB_RECOMMENDED_STATUSES}


class PromoteError(Exception):
    """Selector matched zero or multiple rows; carries a user-facing message."""


@dataclass(frozen=True)
class PromoteResult:
    """Outcome of a promote call.

    ``created`` is False when the URL was already promoted (idempotent no-op);
    ``entry`` is the existing entry in that case. ``dry_run`` carries no entry
    (nothing was written) but does carry the resolved title / status / extras
    so the caller can report what *would* be created.
    """

    created: bool
    entry: TrackerEntry | None
    title: str
    status: str
    extras: dict[str, str]
    already_promoted_id: str | None = None


def _select_row(rows: list[dict[str, str]], selector: str) -> dict[str, str]:
    """Resolve a selector to exactly one jobs.csv row.

    URL exact-match on the Link column is the primary path and wins outright.
    Failing that, a case-insensitive substring match on Company must be
    unambiguous (exactly one row). Zero or multiple matches raise PromoteError
    with a message naming the candidates.
    """
    url_matches = [r for r in rows if (r.get("Link") or "").strip() == selector]
    if url_matches:
        # An exact Link is a unique key; if duplicate Link rows exist take the
        # first (jobs.csv dedups on Link, so this is defensive only).
        return url_matches[0]

    needle = selector.casefold()
    company_matches = [r for r in rows if needle in (r.get("Company") or "").casefold()]
    if len(company_matches) == 1:
        return company_matches[0]

    if not company_matches:
        raise PromoteError(
            f"no jobs.csv row matched {selector!r} "
            "(tried exact Link, then Company substring)"
        )
    listing = "\n".join(
        f"  {(r.get('Company') or '?')} -- {(r.get('Role') or '?')} "
        f"[{(r.get('Link') or '').strip()}]"
        for r in company_matches
    )
    raise PromoteError(
        f"{selector!r} matched {len(company_matches)} rows; "
        f"narrow it (use the full Link URL):\n{listing}"
    )


def _resolve_status(raw_status: str) -> str:
    """Map a jobs.csv Status into the tracker `job` status to store.

    The two vocabularies are identical, so a recognized status carries through
    unchanged. A blank cell (or one outside the job set) falls back to
    `applied` — promotion implies the user is actively driving the row.
    """
    normalized = normalize_status(raw_status)
    if normalized in _JOB_STATUS_SET:
        return normalized
    return _FALLBACK_STATUS


def _find_promoted(tracker: Tracker, url: str) -> TrackerEntry | None:
    """Return an existing `job` entry already linked to ``url``, or None.

    The URL stored in extras is the durable key; matching on it makes promotion
    idempotent across renames of the company/role title.
    """
    for entry in tracker.list(category="job"):
        if (entry.extras.get("url") or "") == url:
            return entry
    return None


def promote(
    tracker: Tracker,
    jobs_csv: Path,
    selector: str,
    *,
    dry_run: bool = False,
) -> PromoteResult:
    """Promote the jobs.csv row matching ``selector`` into a tracker `job` entry.

    Raises PromoteError when the selector matches zero or multiple rows.
    Idempotent: a URL already promoted returns ``created=False`` with the
    existing entry rather than adding a duplicate.
    """
    selector = selector.strip()
    if not selector:
        raise PromoteError("empty selector")

    _header, rows = read_rows(jobs_csv)
    if not rows:
        raise PromoteError(f"no rows in {jobs_csv}")

    row = _select_row(rows, selector)
    company = (row.get("Company") or "").strip()
    role = (row.get("Role") or "").strip()
    url = (row.get("Link") or "").strip()
    source = (row.get("Source") or "").strip()
    status = _resolve_status(row.get("Status") or "")

    title = f"{company or '(unknown)'} -- {role or '(unknown role)'}"
    extras = {
        "url": url,
        "company": company,
        "role": role,
        "source": source,
    }

    existing = _find_promoted(tracker, url) if url else None
    if existing is not None:
        return PromoteResult(
            created=False,
            entry=existing,
            title=existing.title,
            status=existing.status,
            extras=extras,
            already_promoted_id=existing.id,
        )

    if dry_run:
        return PromoteResult(
            created=False, entry=None, title=title, status=status, extras=extras
        )

    entry = tracker.add(
        category="job",
        title=title,
        status=status,
        link=url or None,
        extras=extras,
    )
    return PromoteResult(
        created=True, entry=entry, title=entry.title, status=entry.status, extras=extras
    )
