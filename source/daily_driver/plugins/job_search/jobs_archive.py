"""Archive-table dedup + prune for jobs.csv.

Pruned rows move to ``jobs.archive.csv`` (same schema as ``jobs.csv``). The
scraper unions URLs and dedup-keys from BOTH files at run start so a triaged
listing is never re-discovered.

`prune` selects rows where ``Date Last Seen`` is older than the cutoff AND
``Status`` is in the allowed status set, archives them, then atomically
rewrites ``jobs.csv`` without them via temp-file + ``os.replace``. The read,
classification, archive, and rewrite all run under ``core.locking.file_lock``
so a concurrent scrape cannot append rows between the read and the rewrite
(which would silently delete them). With ``dry_run=True`` no files change.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date
from pathlib import Path

from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.core.statuses import normalize_status
from daily_driver.plugins.job_search.jobs_lock import (
    LOCK_GIVEUP_MESSAGE,
    LOCK_WAIT_TIMEOUT_SECONDS,
    JobsLockTimeout,
    clear_stale_adjacent_lock,
    jobs_lock_path,
    workspace_busy_notice,
)
from daily_driver.plugins.job_search.scraper.csv_io import append_rows as _append_rows
from daily_driver.plugins.job_search.scraper.csv_io import (
    atomic_write_rows as _atomic_write_rows,
)
from daily_driver.plugins.job_search.scraper.csv_io import (
    canonicalize_status_cells,
    dedup_sets_from_rows,
    format_canonicalized_notice,
)
from daily_driver.plugins.job_search.scraper.csv_io import read_rows as _read_rows
from daily_driver.plugins.job_search.scraper.csv_io import (
    warn_unknown_job_statuses,
)

log = get_logger(__name__)

# Job-terminal subset of core.tracker.TERMINAL_STATUSES — the statuses a job
# row reaches and never leaves, so prune archives them by default.
DEFAULT_PRUNE_STATUSES: tuple[str, ...] = ("dropped", "rejected", "closed")


def archive_path_for(jobs_csv: Path) -> Path:
    return jobs_csv.with_name("jobs.archive.csv")


def _parse_iso(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _is_stale(
    row: dict[str, str], *, cutoff: date, statuses: tuple[str, ...] | frozenset[str]
) -> bool:
    """Row qualifies for prune when status matches AND last-seen date is < cutoff.

    Falls back to ``Date Found`` when ``Date Last Seen`` is empty — without an
    on-rescan upsert path, existing rows often only have ``Date Found``.
    """
    # Normalize both sides so a `ruled_out` cell matches a `ruled-out` target.
    status = normalize_status(row.get("Status") or "")
    if status not in {normalize_status(s) for s in statuses}:
        return False
    seen = _parse_iso(row.get("Date Last Seen", "")) or _parse_iso(
        row.get("Date Found", "")
    )
    return seen is not None and seen < cutoff


def prune(
    jobs_csv: Path,
    ephemeral_dir: Path,
    *,
    cutoff: date,
    statuses: tuple[str, ...] | frozenset[str] = DEFAULT_PRUNE_STATUSES,
    dry_run: bool = False,
) -> tuple[list[dict[str, str]], int]:
    """Move stale rows from ``jobs_csv`` to ``jobs.archive.csv``.

    Returns (candidates, archived_count). ``archived_count`` is 0 in dry-run.
    Holds an exclusive flock (sentinel under ``ephemeral_dir``) for the read,
    classification, archive, and rewrite so a concurrent scrape can't append
    rows between the read and the rewrite (which would silently delete them).
    """
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
        header, rows = _read_rows(jobs_csv)
        if not header:
            return [], 0

        keep: list[dict[str, str]] = []
        candidates: list[dict[str, str]] = []
        for row in rows:
            if _is_stale(row, cutoff=cutoff, statuses=statuses):
                candidates.append(row)
            else:
                keep.append(row)

        if dry_run or not candidates:
            return candidates, 0

        # The whole file is being rewritten anyway, so canonicalize the Status
        # spelling of every retained/archived row (ruled_out -> ruled-out).
        # Meaning is never changed. A user did not ask to touch these rows, so
        # surface the spelling fixes as one notice, and warn once over the union
        # (not per slice) on any out-of-vocabulary value.
        if "Status" in header:
            changes = canonicalize_status_cells(keep) + canonicalize_status_cells(
                candidates
            )
            notice = format_canonicalized_notice(changes)
            if notice is not None:
                Console.info(notice)
            warn_unknown_job_statuses([r.get("Status", "") for r in keep + candidates])

        _archive_candidates(archive_path_for(jobs_csv), header, candidates)
        _atomic_write_rows(jobs_csv, header, keep)
    finally:
        lock_stack.close()

    return candidates, len(candidates)


def _archive_candidates(
    archive: Path, header: list[str], candidates: list[dict[str, str]]
) -> None:
    """Append pruned rows to the archive, reconciling a drifted header.

    The archive shares jobs.csv's schema, but the schema drifts across releases
    (e.g. the 0.2.0 Remote-column upgrade reorders/widens the header). A blind
    append writes the new rows under a header that no longer matches the
    archive's own header row, shifting every cell so the archived Link becomes
    unreadable and the triaged job is re-discovered. When the existing archive's
    header differs from jobs.csv's, rewrite the whole archive under the union of
    both column sets (jobs.csv order first, then any archive-only columns) so
    every row — old and new — is keyed correctly. A matching or absent header
    takes the cheap append.
    """
    existing_header, existing_rows = _read_rows(archive)
    if not existing_header or existing_header == header:
        _append_rows(archive, header, candidates)
        return
    union_header = header + [c for c in existing_header if c not in header]
    _atomic_write_rows(archive, union_header, existing_rows + candidates)


def load_archive_dedup(jobs_csv: Path) -> tuple[set[str], set[str]]:
    """Return (urls, dedup_keys) from jobs.archive.csv.

    Empty sets when archive is missing. Reads and extracts dedup state through
    the shared csv path so jobs.csv and jobs.archive.csv stay in lockstep.

    Loud on a malformed-but-present archive: a non-empty file that yields no
    URLs (missing/renamed Link column) would silently let archived/rejected
    jobs be re-discovered, so warn naming the path — the live jobs.csv raises
    for the same defect.
    """
    archive = archive_path_for(jobs_csv)
    header, rows = _read_rows(archive)
    urls, keys = dedup_sets_from_rows(rows)
    if rows and not urls:
        log.warning(
            "%s has %d rows but no usable Link values (column missing or empty); "
            "archived jobs may be re-discovered",
            archive,
            len(rows),
        )
    return urls, keys
