"""CSV read/write helpers for jobs.csv (canonical layout, dedup, backfill)."""

from __future__ import annotations

import csv
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.jobs_lock import jobs_lock_path
from daily_driver.plugins.job_search.scraper.models import (
    ENRICH_SKIP_STATUSES,
    EnrichedJob,
)

if sys.platform != "win32":
    import fcntl

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

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
    """Write rows via temp file + os.replace + fsync to avoid torn reads."""
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
    """
    from daily_driver.plugins.job_search.scraper.runner import ScraperError

    if not jobs:
        return 0

    written = 0
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            if sys.platform != "win32":
                fcntl.flock(f, fcntl.LOCK_EX)
            writer = csv.DictWriter(
                f, fieldnames=header, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
            )
            for job in jobs:
                writer.writerow(job.to_csv_row())
                written += 1
    except OSError as exc:
        raise ScraperError(f"Cannot open {csv_path} for writing: {exc}") from exc
    return written


def _rewrite_jobs_csv(
    csv_path: Path,
    header: list[str],
    jobs: list[EnrichedJob],
) -> None:
    """Rewrite jobs.csv atomically (via .csv.tmp + rename) from typed jobs."""
    tmp_path = csv_path.with_suffix(".csv.tmp")
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=header,
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        for job in jobs:
            writer.writerow(job.to_csv_row())

    tmp_path.rename(csv_path)


def _active(job: EnrichedJob) -> bool:
    return job.status.value not in ENRICH_SKIP_STATUSES


def backfill(ctx: ScrapeContext, csv_path: Path) -> None:
    """Re-enrich existing jobs.csv rows that have empty enrichment fields."""
    from daily_driver.plugins.job_search.scraper.enrichment.llm import (
        enrich_company_descriptions,
        enrich_fit_and_notes,
    )
    from daily_driver.plugins.job_search.scraper.runner import ScraperError

    if not csv_path.exists():
        raise ScraperError(f"jobs.csv not found at {csv_path}")

    # Hold one shared sentinel lock for the entire backfill lifecycle so run/
    # prune cannot interleave while we enrich in memory then rewrite.
    with file_lock(jobs_lock_path(csv_path)):
        with open(csv_path, newline="", encoding="utf-8") as lock_fh:
            reader = csv.DictReader(lock_fh)
            header = list(reader.fieldnames or CANONICAL_HEADER)
            rows = list(reader)

        jobs = [EnrichedJob.from_csv_row(r) for r in rows]

        active = [j for j in jobs if _active(j)]
        needs_product = sum(1 for j in active if not j.product_filled)
        needs_fit = sum(1 for j in active if not j.fit)
        needs_gd = sum(1 for j in active if not j.gd_rating)
        needs_notes = sum(1 for j in active if not j.notes)
        skipped_count = len(jobs) - len(active)

        log.info(
            "[backfill] %d rows (%d skipped excluded): "
            "%d need Product, %d need GD, %d need Fit, %d need Notes",
            len(jobs),
            skipped_count,
            needs_product,
            needs_gd,
            needs_fit,
            needs_notes,
        )

        if not (needs_product or needs_fit or needs_gd or needs_notes):
            print("All rows already enriched, nothing to backfill.")
            return

        # One pre-mutation snapshot: covers both crash recovery (on interrupt)
        # and undo (after a successful but unwanted enrichment).
        backup = _make_backup(csv_path)
        log.info("[backfill] backed up to %s", backup.name)

        try:
            jobs, _ = enrich_company_descriptions(jobs, ctx, budget=0)
            jobs, _ = enrich_fit_and_notes(jobs, ctx, budget=0)
        except KeyboardInterrupt:
            try:
                _rewrite_jobs_csv(csv_path, header, jobs)
                save_status = (
                    f"partial progress saved to jobs.csv "
                    f"(original preserved at {backup.name})"
                )
            except OSError as exc:
                # Disk full / permission denied / etc. Original jobs.csv is
                # intact (atomic-rename only fires on a successful tmp write)
                # and the .bak from before this run still exists, so the
                # user has a clean rollback path even when we can't write.
                save_status = (
                    f"could not save partial progress ({exc}). "
                    f"Original preserved at {backup.name}"
                )
            print(f"Backfill interrupted: {save_status}", file=sys.stderr)
            raise

        _rewrite_jobs_csv(csv_path, header, jobs)

    active = [j for j in jobs if _active(j)]
    filled_product = needs_product - sum(1 for j in active if not j.product_filled)
    filled_fit = needs_fit - sum(1 for j in active if not j.fit)
    filled_gd = needs_gd - sum(1 for j in active if not j.gd_rating)
    filled_notes = needs_notes - sum(1 for j in active if not j.notes)
    print(
        f"Backfill complete: +{filled_product} Product, +{filled_gd} GD, "
        f"+{filled_fit} Fit, +{filled_notes} Notes"
    )


__all__ = [
    "CANONICAL_HEADER",
    "load_existing_jobs",
    "append_jobs_typed",
    "backfill",
    "read_rows",
    "atomic_write_rows",
    "append_rows",
    "dedup_sets_from_rows",
]
