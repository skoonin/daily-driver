"""Archive-table dedup + prune for jobs.csv.

Pruned rows move to ``jobs.archive.csv`` (same schema as ``jobs.csv``). The
scraper unions URLs and dedup-keys from BOTH files at run start so a triaged
listing is never re-discovered.

`prune` selects rows where ``Date Last Seen`` is older than the cutoff AND
``Status`` is in the allowed status set, moves them to the archive, and
rewrites ``jobs.csv`` without them. With ``dry_run=True`` no files change.
"""

from __future__ import annotations

import csv
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

if sys.platform != "win32":
    import fcntl


@dataclass(frozen=True)
class PruneCandidate:
    company: str
    role: str
    status: str
    date_last_seen: str
    link: str


@dataclass(frozen=True)
class PruneResult:
    candidates: list[PruneCandidate]
    archived: int  # 0 when dry_run


DEFAULT_PRUNE_STATUSES: frozenset[str] = frozenset({"dropped", "rejected", "closed"})


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


def _write_rows(csv_path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        if sys.platform != "win32":
            fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.DictWriter(
            f, fieldnames=header, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _append_rows(csv_path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        if sys.platform != "win32":
            fcntl.flock(f, fcntl.LOCK_EX)
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


def select_candidates(
    rows: list[dict[str, str]],
    *,
    cutoff: date,
    statuses: frozenset[str],
) -> list[tuple[int, dict[str, str]]]:
    """Return [(row_index, row)] for rows older than cutoff and matching statuses.

    Age is read from ``Date Last Seen`` when present; falls back to ``Date Found``
    so older rows (pre Date-Last-Seen migration) still prune cleanly.
    """
    out: list[tuple[int, dict[str, str]]] = []
    for idx, row in enumerate(rows):
        status = (row.get("Status") or "").strip().lower()
        if status not in statuses:
            continue
        seen = _parse_iso(row.get("Date Last Seen", "")) or _parse_iso(
            row.get("Date Found", "")
        )
        if seen is None:
            continue
        if seen < cutoff:
            out.append((idx, row))
    return out


def to_candidate(row: dict[str, str]) -> PruneCandidate:
    return PruneCandidate(
        company=row.get("Company", ""),
        role=row.get("Role", ""),
        status=row.get("Status", ""),
        date_last_seen=row.get("Date Last Seen", "") or row.get("Date Found", ""),
        link=row.get("Link", ""),
    )


def prune(
    jobs_csv: Path,
    *,
    cutoff: date,
    statuses: frozenset[str] = DEFAULT_PRUNE_STATUSES,
    dry_run: bool = False,
) -> PruneResult:
    """Move rows from ``jobs_csv`` to ``jobs.archive.csv`` when stale + matching status."""
    header, rows = _read_rows(jobs_csv)
    if not header:
        return PruneResult(candidates=[], archived=0)

    selected = select_candidates(rows, cutoff=cutoff, statuses=statuses)
    candidates = [to_candidate(row) for _, row in selected]

    if dry_run or not selected:
        return PruneResult(candidates=candidates, archived=0)

    backup = jobs_csv.with_suffix(f".csv.bak.{int(time.time())}")
    shutil.copy2(jobs_csv, backup)

    drop = {idx for idx, _ in selected}
    keep = [row for i, row in enumerate(rows) if i not in drop]
    archived_rows = [row for _, row in selected]

    archive = archive_path_for(jobs_csv)
    _append_rows(archive, header, archived_rows)
    _write_rows(jobs_csv, header, keep)

    return PruneResult(candidates=candidates, archived=len(archived_rows))


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
