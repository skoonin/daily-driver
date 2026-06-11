"""CSV read/write helpers for jobs.csv (canonical layout, dedup, backups)."""

from __future__ import annotations

import csv
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.models import (
    ENRICH_SKIP_STATUSES,
    EnrichedJob,
)

log = get_logger(__name__)


# The single source of truth for jobs.csv column order, derived from the model.
CANONICAL_HEADER = EnrichedJob.CANONICAL_HEADER


def _make_backup(csv_path: Path) -> Path:
    """Snapshot jobs.csv into <output_dir>/backups/ with a UTC ISO-8601 stamp.

    Filename pattern: jobs.csv.bak.YYYY-MM-DDTHH-MM-SS-ffffffZ. Colons in the
    time portion are replaced with hyphens so the filename is portable across
    filesystems (Windows forbids ':'). Microseconds prevent same-second
    collisions when backfill writes back-to-back snapshots under the jobs lock.
    """
    backups_dir = csv_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    backup = backups_dir / f"{csv_path.name}.bak.{stamp}"
    shutil.copy2(csv_path, backup)
    return backup


def read_rows(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return (header_columns, row_dicts) for a jobs-shaped CSV.

    Empty (header, rows) when the file is absent. Shared by archive prune and
    any caller that needs the rows as dicts keyed by CSV column.
    """
    if not csv_path.exists():
        return [], []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return header, rows


def atomic_write_rows(
    csv_path: Path, header: list[str], rows: list[dict[str, str]]
) -> None:
    """Rewrite a CSV atomically: temp file + flush/fsync + os.replace.

    The single rewrite path for jobs.csv and jobs.archive.csv. On a mid-write
    failure the partial temp file is unlinked before the error propagates, so a
    crash never leaves a stray ``.tmp`` beside the data file.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    try:
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
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def append_rows(csv_path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    """Append row dicts to a CSV, writing the header first if the file is new."""
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


def dedup_sets_from_rows(rows: list[dict[str, str]]) -> tuple[set[str], set[str]]:
    """Extract (urls, dedup_keys) from row dicts keyed by CSV column.

    Mirrors load_existing_jobs' extraction so jobs.csv and jobs.archive.csv
    contribute dedup state through one code path.
    """
    from daily_driver.plugins.job_search.scraper.runner import dedup_key

    urls: set[str] = set()
    keys: set[str] = set()
    for row in rows:
        link = (row.get("Link") or "").strip()
        if link:
            urls.add(link)
        company = (row.get("Company") or "").strip()
        role = (row.get("Role") or "").strip()
        if company or role:
            keys.add(dedup_key(company, role))
    return urls, keys


def load_existing_jobs(csv_path: Path) -> tuple[set[str], set[str], list[str]]:
    """Return (known_urls, known_keys, header_columns).

    known_urls  — set of Link column values, for URL-based dedup.
    known_keys  — set of dedup_key(company, role) strings, for cross-site dedup.
    """
    from daily_driver.plugins.job_search.scraper.runner import ScraperError, dedup_key

    if not csv_path.exists():
        return set(), set(), []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                return set(), set(), []
            if "Link" not in header:
                raise ScraperError(
                    "jobs.csv is missing required 'Link' column — cannot deduplicate"
                )
            link_idx = header.index("Link")
            company_idx = header.index("Company") if "Company" in header else None
            role_idx = header.index("Role") if "Role" in header else None
            known_urls: set[str] = set()
            known_keys: set[str] = set()
            for row in reader:
                if not row:
                    continue
                if link_idx < len(row) and row[link_idx]:
                    known_urls.add(row[link_idx].strip())
                company = (
                    row[company_idx].strip()
                    if company_idx is not None and company_idx < len(row)
                    else ""
                )
                role = (
                    row[role_idx].strip()
                    if role_idx is not None and role_idx < len(row)
                    else ""
                )
                if company or role:
                    known_keys.add(dedup_key(company, role))
    except OSError as exc:
        raise ScraperError(f"Cannot read {csv_path}: {exc}") from exc
    return known_urls, known_keys, header


def append_jobs_typed(
    csv_path: Path,
    jobs: list[EnrichedJob],
    header: list[str],
) -> int:
    """Typed CSV writer: one row per ``EnrichedJob.to_csv_row()``.

    Header is still passed in so callers can pin the column order to whatever
    is in ``jobs.csv`` today (legacy migrations may have re-ordered columns).
    Extra keys produced by ``to_csv_row`` are dropped via ``extrasaction='ignore'``.

    Serializing concurrent mutations is the caller's contract: hold
    ``file_lock(jobs_lock_path(...))`` around this call. This function does not
    lock.
    """
    from daily_driver.plugins.job_search.scraper.runner import ScraperError

    if not jobs:
        return 0

    written = 0
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=header, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
            )
            for job in jobs:
                writer.writerow(job.to_csv_row())
                written += 1
    except OSError as exc:
        raise ScraperError(f"Cannot open {csv_path} for writing: {exc}") from exc
    return written


def _active(job: EnrichedJob) -> bool:
    return job.status.value not in ENRICH_SKIP_STATUSES


__all__ = [
    "CANONICAL_HEADER",
    "load_existing_jobs",
    "append_jobs_typed",
    "read_rows",
    "atomic_write_rows",
    "append_rows",
    "dedup_sets_from_rows",
]
