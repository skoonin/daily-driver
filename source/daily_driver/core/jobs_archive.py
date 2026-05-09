"""Archive-table dedup + prune for jobs.csv.

Pruned rows move to ``jobs.archive.csv`` (same schema as ``jobs.csv``). The
scraper unions URLs and dedup-keys from BOTH files at run start so a triaged
listing is never re-discovered.

`prune` selects rows where ``Date Last Seen`` is older than the cutoff AND
``Status`` is in the allowed status set, archives them, then atomically
rewrites ``jobs.csv`` without them via temp-file + ``os.replace``. The
write is held under ``core.locking.file_lock`` so concurrent invocations
don't race. With ``dry_run=True`` no files change.
"""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

from daily_driver.core.locking import file_lock

DEFAULT_PRUNE_STATUSES: tuple[str, ...] = ("dropped", "rejected", "closed")


def archive_path_for(jobs_csv: Path) -> Path:
    return jobs_csv.with_name("jobs.archive.csv")


def _read_rows(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not csv_path.exists():
        return [], []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return header, rows


def _atomic_write_rows(
    csv_path: Path, header: list[str], rows: list[dict[str, str]]
) -> None:
    """Write rows via temp file + os.replace to avoid mid-write torn reads."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=header, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, csv_path)


def _append_rows(csv_path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=header, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
        )
        if fresh:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


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

    Falls back to ``Date Found`` when ``Date Last Seen`` is empty — the on-rescan
    upsert path is owned by W5/W6, so existing rows often only have ``Date Found``.
    """
    status = (row.get("Status") or "").strip().lower()
    if status not in statuses:
        return False
    seen = _parse_iso(row.get("Date Last Seen", "")) or _parse_iso(
        row.get("Date Found", "")
    )
    return seen is not None and seen < cutoff


def prune(
    jobs_csv: Path,
    *,
    cutoff: date,
    statuses: tuple[str, ...] | frozenset[str] = DEFAULT_PRUNE_STATUSES,
    dry_run: bool = False,
) -> tuple[list[dict[str, str]], int]:
    """Move stale rows from ``jobs_csv`` to ``jobs.archive.csv``.

    Returns (candidates, archived_count). ``archived_count`` is 0 in dry-run.
    Holds an exclusive flock on ``jobs_csv`` for the entire archive+rewrite
    sequence so a concurrent scrape can't observe a torn state.
    """
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

    with file_lock(jobs_csv):
        _append_rows(archive_path_for(jobs_csv), header, candidates)
        _atomic_write_rows(jobs_csv, header, keep)

    return candidates, len(candidates)


def load_archive_dedup(jobs_csv: Path) -> tuple[set[str], set[str]]:
    """Return (urls, dedup_keys) from jobs.archive.csv.

    Empty sets when archive is missing. ``dedup_key`` import is local to avoid
    a circular dependency on the scraper package at module load time.
    """
    archive = archive_path_for(jobs_csv)
    if not archive.exists():
        return set(), set()

    from daily_driver.scraper._impl import dedup_key

    urls: set[str] = set()
    keys: set[str] = set()
    with open(archive, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            link = (row.get("Link") or "").strip()
            if link:
                urls.add(link)
            company = (row.get("Company") or "").strip()
            role = (row.get("Role") or "").strip()
            if company or role:
                keys.add(dedup_key(company, role))
    return urls, keys
