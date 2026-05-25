"""CSV read/write helpers for jobs.csv (canonical layout, dedup, backfill)."""

from __future__ import annotations

import csv
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from daily_driver.core.clock import today
from daily_driver.core.jobs_lock import jobs_lock_path
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger

if sys.platform != "win32":
    import fcntl

if TYPE_CHECKING:
    from daily_driver.scraper.models import EnrichedJob

log = get_logger(__name__)


CANONICAL_HEADER = [
    "Status",
    "Company",
    "Role",
    "Fit",
    "Comp",
    "Location",
    "Product/Purpose",
    "GD Rating",
    "Notes",
    "Date Found",
    "Date Applied",
    # Date Last Seen drives `jobs prune --older-than`. Today scraper
    # only sets it on insert (defaults to Date Found); without an
    # upsert-on-rescan path, prune ages from first-discovery.
    "Date Last Seen",
    "Link",
    "Source",
]


def _make_backup(csv_path: Path) -> Path:
    """Snapshot jobs.csv into <output_dir>/backups/ with a UTC ISO-8601 stamp.

    Filename pattern: jobs.csv.bak.YYYY-MM-DDTHH-MM-SS-ffffffZ. Colons in the
    time portion are replaced with hyphens so the filename is portable across
    filesystems (Windows forbids ':'). Microseconds prevent same-second
    collisions when two callers (migration + backfill) run back-to-back under
    the same jobs lock.
    """
    backups_dir = csv_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    backup = backups_dir / f"{csv_path.name}.bak.{stamp}"
    shutil.copy2(csv_path, backup)
    return backup


def _migrate_legacy_header(csv_path: Path, current_header: list[str]) -> list[str]:
    """Rewrite jobs.csv to the canonical header + status taxonomy.

    Two on-disk migrations live here:
      1. legacy '#'-first header  → CANONICAL_HEADER
      2. legacy Status = 'archived' → 'dropped' (JobStatus rename)

    Creates a backup under <output_dir>/backups/ so the migration is reversible.
    Idempotent: if the header is already canonical AND no rows still carry the
    legacy status value, this is a no-op and no backup is written.
    """
    needs_header_rewrite = current_header != CANONICAL_HEADER

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rewritten = 0
    for row in rows:
        if row.get("Status") == "archived":
            row["Status"] = "dropped"
            rewritten += 1

    if not (needs_header_rewrite or rewritten):
        return CANONICAL_HEADER

    backup = _make_backup(csv_path)
    log.info("[migrate] jobs.csv backed up to %s", backup.name)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CANONICAL_HEADER,
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            row.pop("#", None)
            writer.writerow(row)
    if needs_header_rewrite:
        log.info("[migrate] jobs.csv rewritten to new column layout")
    if rewritten:
        log.info("[migrate] %d row(s) rewritten: status archived → dropped", rewritten)
    return CANONICAL_HEADER


def load_existing_jobs(csv_path: Path) -> tuple[set[str], set[str], list[str]]:
    """Return (known_urls, known_keys, header_columns).

    known_urls  — set of Link column values, for URL-based dedup.
    known_keys  — set of dedup_key(company, role) strings, for cross-site dedup.
    """
    from daily_driver.scraper.runner import ScraperError, dedup_key

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


def append_jobs(csv_path: Path, jobs: list[dict], header: list[str]) -> int:
    """Append new jobs to CSV. Returns count of rows written.

    Legacy dict-based entry point. Typed callers should use
    ``append_jobs_typed`` which routes through ``EnrichedJob.to_csv_row()``.
    """
    from daily_driver.scraper.runner import ScraperError

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
                row = {col: "" for col in header}
                row["Company"] = job.get("company", "")
                row["Product/Purpose"] = job.get(
                    "product", "(auto-scraped -- needs fill)"
                )
                row["Role"] = job.get("role", "")
                row["Comp"] = job.get("comp", "")
                row["Location"] = job.get("location", "")
                row["Source"] = job.get("source", "")
                row["Date Found"] = job.get("date_found", today().isoformat())
                row["Date Last Seen"] = job.get("date_last_seen", row["Date Found"])
                row["Status"] = job.get("status") or "found"
                row["Link"] = job.get("url", "")
                row["Fit"] = job.get("fit", "")
                row["GD Rating"] = job.get("gd_rating", "")
                row["Notes"] = job.get("notes", "")
                writer.writerow(row)
                written += 1
    except OSError as exc:
        raise ScraperError(f"Cannot open {csv_path} for writing: {exc}") from exc
    return written


def append_jobs_typed(
    csv_path: Path,
    jobs: list[EnrichedJob],  # noqa: F821
    header: list[str],
) -> int:
    """Typed CSV writer: one row per ``EnrichedJob.to_csv_row()``.

    Header is still passed in so callers can pin the column order to whatever
    is in ``jobs.csv`` today (legacy migrations may have re-ordered columns).
    Extra keys produced by ``to_csv_row`` are dropped via ``extrasaction='ignore'``.
    """
    from daily_driver.scraper.runner import ScraperError

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


def _enriched_to_dict(job: EnrichedJob) -> dict[str, Any]:  # noqa: F821
    """Project an EnrichedJob into the legacy working-dict shape.

    Used by typed enricher wrappers so existing dict-based enricher bodies
    (which mutate in place) can run unchanged.
    """
    return {
        "company": job.company,
        "role": job.role,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "source_canonical": job.source_canonical,
        "source_board": job.source_board,
        "comp": str(job.comp),
        "date_found": job.date_found.isoformat(),
        "product": job.product,
        "gd_rating": job.gd_rating,
        "fit": "" if job.fit is None else job.fit,
        "notes": job.notes,
        "description_text": job.description_text,
        "status": job.status.value,
    }


def _dict_to_enriched_updates(d: dict[str, Any]) -> dict[str, Any]:
    """Pick the enricher-mutated fields out of the working dict.

    Returned dict is suitable for ``EnrichedJob.model_copy(update=...)``.
    """
    from daily_driver.scraper.models import Comp, JobStatus

    updates: dict[str, Any] = {}
    if "product" in d:
        updates["product"] = d["product"]
    if "gd_rating" in d:
        updates["gd_rating"] = d["gd_rating"]
    if "fit" in d:
        f = d["fit"]
        updates["fit"] = (
            int(f)
            if isinstance(f, int) or (isinstance(f, str) and f.isdigit())
            else None
        )
    if "notes" in d:
        updates["notes"] = d["notes"]
    if "description_text" in d:
        updates["description_text"] = d["description_text"]
    if "status" in d and d["status"]:
        updates["status"] = JobStatus(d["status"])
    if "skip_reason" in d:
        updates["skip_reason"] = d["skip_reason"]
    if "comp" in d and isinstance(d["comp"], str) and d["comp"]:
        # Re-parse only when the enricher updated the display string.
        new_comp = Comp.parse(d["comp"])
        updates["comp"] = new_comp
    return updates


# Column mapping: CSV header name -> internal dict key
_CSV_TO_DICT = {
    "Status": "status",
    "Company": "company",
    "Product/Purpose": "product",
    "Role": "role",
    "Comp": "comp",
    "Location": "location",
    "Fit": "fit",
    "GD Rating": "gd_rating",
    "Source": "source",
    "Date Found": "date_found",
    "Date Applied": "date_applied",
    "Link": "url",
    "Notes": "notes",
}

_DICT_TO_CSV = {v: k for k, v in _CSV_TO_DICT.items()}

_PLACEHOLDER_PRODUCT = "(auto-scraped -- needs fill)"


def _row_to_dict(row: dict[str, str]) -> dict[str, str]:
    """Convert a CSV DictReader row to our internal job dict format."""
    out: dict[str, str] = {}
    for csv_col, dict_key in _CSV_TO_DICT.items():
        val = (row.get(csv_col) or "").strip()
        if dict_key == "product" and val == _PLACEHOLDER_PRODUCT:
            val = ""
        out[dict_key] = val
    return out


def _dict_to_row(job: dict[str, str], header: list[str]) -> dict[str, str]:
    """Convert an internal job dict back to a CSV row dict."""
    row = {col: "" for col in header}
    for dict_key, csv_col in _DICT_TO_CSV.items():
        if csv_col in row:
            row[csv_col] = job.get(dict_key, "")
    return row


def _rewrite_jobs_csv(
    csv_path: Path,
    header: list[str],
    jobs: list[dict[str, str]],
) -> None:
    """Rewrite jobs.csv atomically (via .csv.tmp + rename) from in-memory rows."""
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
            writer.writerow(_dict_to_row(job, header))

    tmp_path.rename(csv_path)


def backfill(config: dict, csv_path: Path) -> None:
    """Re-enrich existing jobs.csv rows that have empty enrichment fields."""
    from daily_driver.scraper.enrichment import (
        enrich_company_descriptions,
        enrich_fit_and_notes,
    )
    from daily_driver.scraper.runner import ScraperError, validate_config

    validate_config(config)
    if not csv_path.exists():
        raise ScraperError(f"jobs.csv not found at {csv_path}")

    # Hold one shared sentinel lock for the entire backfill lifecycle so run/
    # prune cannot interleave while we enrich in memory then rewrite.
    with file_lock(jobs_lock_path(csv_path)):
        with open(csv_path, newline="", encoding="utf-8") as lock_fh:
            reader = csv.DictReader(lock_fh)
            header = list(reader.fieldnames or CANONICAL_HEADER)
            rows = list(reader)

        jobs = [_row_to_dict(r) for r in rows]

        active = [j for j in jobs if j.get("status") != "skipped"]
        needs_product = sum(1 for j in active if not j.get("product"))
        needs_fit = sum(1 for j in active if not j.get("fit"))
        needs_gd = sum(1 for j in active if not j.get("gd_rating"))
        needs_notes = sum(1 for j in active if not j.get("notes"))
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
            enrich_company_descriptions(jobs, config, budget=0)
            enrich_fit_and_notes(jobs, config, budget=0)
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

    filled_product = needs_product - sum(1 for j in active if not j.get("product"))
    filled_fit = needs_fit - sum(1 for j in active if not j.get("fit"))
    filled_gd = needs_gd - sum(1 for j in active if not j.get("gd_rating"))
    filled_notes = needs_notes - sum(1 for j in active if not j.get("notes"))
    print(
        f"Backfill complete: +{filled_product} Product, +{filled_gd} GD, "
        f"+{filled_fit} Fit, +{filled_notes} Notes"
    )


__all__ = [
    "CANONICAL_HEADER",
    "_migrate_legacy_header",
    "load_existing_jobs",
    "append_jobs",
    "append_jobs_typed",
    "_CSV_TO_DICT",
    "_DICT_TO_CSV",
    "_PLACEHOLDER_PRODUCT",
    "_row_to_dict",
    "_dict_to_row",
    "backfill",
    "_enriched_to_dict",
    "_dict_to_enriched_updates",
]
