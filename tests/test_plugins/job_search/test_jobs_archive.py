"""Tests for daily_driver.plugins.job_search.jobs_archive — prune locking + race safety."""

from __future__ import annotations

import csv
import multiprocessing
from datetime import date
from pathlib import Path
from typing import Any

from daily_driver.core.locking import file_lock
from daily_driver.plugins.job_search import jobs_archive
from daily_driver.plugins.job_search.jobs_lock import jobs_lock_path

HEADER = [
    "Status",
    "Company",
    "Role",
    "Date Found",
    "Date Last Seen",
    "Link",
]


def _row(*, company: str, link: str, status: str, last_seen: str) -> dict[str, str]:
    return {
        "Status": status,
        "Company": company,
        "Role": "SRE",
        "Date Found": last_seen,
        "Date Last Seen": last_seen,
        "Link": link,
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# Module-level so multiprocessing spawn can pickle it. Acquires the shared jobs
# lock and appends a fresh (non-stale) row — modelling a concurrent scrape.
def _appender_worker(csv_path: str, ready: Any, go: Any) -> None:
    path = Path(csv_path)
    ready.set()
    go.wait(timeout=10)
    with file_lock(jobs_lock_path(path)):
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
            writer.writerow(
                _row(
                    company="NewCo",
                    link="https://example.com/new",
                    status="found",
                    last_seen="2026-05-20",
                )
            )


def test_concurrent_prune_and_append_keeps_new_row(tmp_path: Path) -> None:
    """A row appended by a concurrent scrape must survive a prune.

    Before R3 the prune read+classified rows outside the lock, so a row
    appended between the read and the rewrite was silently dropped by the
    stale-read rewrite. With the read moved inside the lock the two operations
    serialize and the appended row is never lost.
    """
    csv_path = tmp_path / "jobs.csv"
    _write_csv(
        csv_path,
        [
            _row(
                company="OldCo",
                link="https://example.com/old",
                status="rejected",
                last_seen="2026-04-01",
            ),
            _row(
                company="KeepCo",
                link="https://example.com/keep",
                status="found",
                last_seen="2026-05-20",
            ),
        ],
    )

    ready = multiprocessing.Event()
    go = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_appender_worker, args=(str(csv_path), ready, go)
    )
    proc.start()
    assert ready.wait(timeout=5)

    go.set()
    # Run prune in the parent while the worker contends for the same lock.
    # Whichever wins, the appended row must end up in jobs.csv.
    candidates, archived = jobs_archive.prune(csv_path, cutoff=date(2026, 5, 1))
    proc.join(timeout=10)
    assert proc.exitcode == 0

    links = {r["Link"] for r in _read_csv(csv_path)}
    assert "https://example.com/new" in links, "concurrently-appended row was deleted"
    assert "https://example.com/keep" in links
    assert "https://example.com/old" not in links

    archived_links = {
        r["Link"] for r in _read_csv(jobs_archive.archive_path_for(csv_path))
    }
    assert archived_links == {"https://example.com/old"}


def test_prune_dry_run_does_not_mutate(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_csv(
        csv_path,
        [
            _row(
                company="OldCo",
                link="https://example.com/old",
                status="rejected",
                last_seen="2026-04-01",
            )
        ],
    )
    before = csv_path.read_text(encoding="utf-8")

    candidates, archived = jobs_archive.prune(
        csv_path, cutoff=date(2026, 5, 1), dry_run=True
    )

    assert archived == 0
    assert len(candidates) == 1
    assert csv_path.read_text(encoding="utf-8") == before
    assert not jobs_archive.archive_path_for(csv_path).exists()


def test_prune_holds_lock_across_read(tmp_path: Path, monkeypatch: Any) -> None:
    """The read must happen inside the lock, not before it."""
    csv_path = tmp_path / "jobs.csv"
    _write_csv(
        csv_path,
        [
            _row(
                company="OldCo",
                link="https://example.com/old",
                status="rejected",
                last_seen="2026-04-01",
            )
        ],
    )

    events: list[str] = []
    real_read = jobs_archive._read_rows
    real_lock = jobs_archive.file_lock

    from contextlib import contextmanager

    @contextmanager
    def tracking_lock(path: Path, **kwargs: Any):
        events.append("lock-enter")
        with real_lock(path, **kwargs):
            yield
        events.append("lock-exit")

    def tracking_read(path: Path):
        events.append("read")
        return real_read(path)

    monkeypatch.setattr(jobs_archive, "file_lock", tracking_lock)
    monkeypatch.setattr(jobs_archive, "_read_rows", tracking_read)

    jobs_archive.prune(csv_path, cutoff=date(2026, 5, 1))

    assert events[0] == "lock-enter"
    assert events.index("read") > events.index("lock-enter")
    assert events.index("read") < events.index("lock-exit")


# ---------------------------------------------------------------------------
# _parse_iso — date parsing tolerance
# ---------------------------------------------------------------------------


def test_parse_iso_plain_date() -> None:
    assert jobs_archive._parse_iso("2026-05-20") == date(2026, 5, 20)


def test_parse_iso_truncates_timestamp_to_date() -> None:
    assert jobs_archive._parse_iso("2026-05-20T14:33:00Z") == date(2026, 5, 20)


def test_parse_iso_strips_whitespace() -> None:
    assert jobs_archive._parse_iso("  2026-05-20  ") == date(2026, 5, 20)


def test_parse_iso_empty_is_none() -> None:
    assert jobs_archive._parse_iso("") is None
    assert jobs_archive._parse_iso("   ") is None


def test_parse_iso_malformed_is_none() -> None:
    assert jobs_archive._parse_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# _is_stale — cutoff + status classification
# ---------------------------------------------------------------------------

_CUTOFF = date(2026, 5, 1)


def test_is_stale_old_and_pruneable_status() -> None:
    row = _row(company="X", link="x", status="rejected", last_seen="2026-04-01")
    assert jobs_archive._is_stale(
        row, cutoff=_CUTOFF, statuses=jobs_archive.DEFAULT_PRUNE_STATUSES
    )


def test_is_stale_status_not_in_set_keeps_row() -> None:
    row = _row(company="X", link="x", status="found", last_seen="2026-04-01")
    assert not jobs_archive._is_stale(
        row, cutoff=_CUTOFF, statuses=jobs_archive.DEFAULT_PRUNE_STATUSES
    )


def test_is_stale_status_is_case_insensitive() -> None:
    row = _row(company="X", link="x", status="REJECTED", last_seen="2026-04-01")
    assert jobs_archive._is_stale(
        row, cutoff=_CUTOFF, statuses=jobs_archive.DEFAULT_PRUNE_STATUSES
    )


def test_is_stale_cutoff_is_exclusive_on_boundary() -> None:
    # last_seen == cutoff is NOT stale (criterion is strictly < cutoff).
    row = _row(company="X", link="x", status="rejected", last_seen="2026-05-01")
    assert not jobs_archive._is_stale(
        row, cutoff=_CUTOFF, statuses=jobs_archive.DEFAULT_PRUNE_STATUSES
    )


def test_is_stale_recent_date_keeps_row() -> None:
    row = _row(company="X", link="x", status="rejected", last_seen="2026-05-20")
    assert not jobs_archive._is_stale(
        row, cutoff=_CUTOFF, statuses=jobs_archive.DEFAULT_PRUNE_STATUSES
    )


def test_is_stale_falls_back_to_date_found_when_last_seen_empty() -> None:
    row = {
        "Status": "rejected",
        "Company": "X",
        "Role": "SRE",
        "Date Found": "2026-04-01",
        "Date Last Seen": "",
        "Link": "x",
    }
    assert jobs_archive._is_stale(
        row, cutoff=_CUTOFF, statuses=jobs_archive.DEFAULT_PRUNE_STATUSES
    )


def test_is_stale_no_usable_date_keeps_row() -> None:
    row = {
        "Status": "rejected",
        "Company": "X",
        "Role": "SRE",
        "Date Found": "",
        "Date Last Seen": "",
        "Link": "x",
    }
    assert not jobs_archive._is_stale(
        row, cutoff=_CUTOFF, statuses=jobs_archive.DEFAULT_PRUNE_STATUSES
    )


def test_is_stale_honors_custom_status_set() -> None:
    row = _row(company="X", link="x", status="archived", last_seen="2026-04-01")
    assert jobs_archive._is_stale(row, cutoff=_CUTOFF, statuses=("archived",))
    assert not jobs_archive._is_stale(
        row, cutoff=_CUTOFF, statuses=jobs_archive.DEFAULT_PRUNE_STATUSES
    )


# ---------------------------------------------------------------------------
# prune — end-to-end classification (no concurrency)
# ---------------------------------------------------------------------------


def test_prune_partitions_stale_and_kept_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_csv(
        csv_path,
        [
            _row(company="Old", link="old", status="rejected", last_seen="2026-04-01"),
            _row(
                company="Recent", link="rec", status="rejected", last_seen="2026-05-20"
            ),
            _row(company="Active", link="act", status="found", last_seen="2026-04-01"),
        ],
    )

    candidates, archived = jobs_archive.prune(csv_path, cutoff=_CUTOFF)

    assert archived == 1
    assert {r["Link"] for r in candidates} == {"old"}
    assert {r["Link"] for r in _read_csv(csv_path)} == {"rec", "act"}
    assert {r["Link"] for r in _read_csv(jobs_archive.archive_path_for(csv_path))} == {
        "old"
    }


def test_prune_missing_csv_returns_empty(tmp_path: Path) -> None:
    csv_path = tmp_path / "absent.csv"

    candidates, archived = jobs_archive.prune(csv_path, cutoff=_CUTOFF)

    assert candidates == []
    assert archived == 0


def test_prune_no_candidates_leaves_files_untouched(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_csv(
        csv_path,
        [_row(company="Active", link="act", status="found", last_seen="2026-04-01")],
    )

    candidates, archived = jobs_archive.prune(csv_path, cutoff=_CUTOFF)

    assert candidates == []
    assert archived == 0
    assert not jobs_archive.archive_path_for(csv_path).exists()
