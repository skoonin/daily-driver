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

from datetime import date
from pathlib import Path

from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.core.statuses import normalize_status
from daily_driver.plugins.job_search.jobs_lock import (
    clear_stale_adjacent_lock,
    jobs_lock_path,
)
from daily_driver.plugins.job_search.scraper.csv_io import append_rows as _append_rows
from daily_driver.plugins.job_search.scraper.csv_io import (
    atomic_write_rows as _atomic_write_rows,
)
from daily_driver.plugins.job_search.scraper.csv_io import (
    dedup_sets_from_rows,
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


def _normalize_status_cells(rows: list[dict[str, str]]) -> None:
    """Canonicalize the Status spelling of each row in place; warn once on unknowns."""
    for row in rows:
        raw = row.get("Status")
        if raw:
            row["Status"] = normalize_status(raw)
    warn_unknown_job_statuses([r.get("Status", "") for r in rows])


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
    with file_lock(jobs_lock_path(ephemeral_dir)):
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
        # spelling of every retained/archived row (ruled_out -> ruled-out) and
        # surface any out-of-vocabulary values once. Meaning is never changed.
        if "Status" in header:
            _normalize_status_cells(keep)
            _normalize_status_cells(candidates)

        _append_rows(archive_path_for(jobs_csv), header, candidates)
        _atomic_write_rows(jobs_csv, header, keep)

    return candidates, len(candidates)


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
