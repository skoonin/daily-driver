"""One-time jobs.csv schema migration: Date Last Seen -> Date Verified + Date Closed.

Neither write path performs a clean rename on its own: the run path reuses the
on-disk header (new-column writes silently dropped by extrasaction="ignore"),
and backfill would classify the old column as an unknown extra (stray 16th
column). migrate_legacy_header upgrades the file in place, once, at the entry
of every write path (run, backfill, prune).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.csv_io import (
    CANONICAL_HEADER,
    migrate_legacy_header,
    read_rows,
)
from daily_driver.plugins.job_search.scraper.models import EnrichedJob

_LEGACY_HEADER = [
    "Status",
    "Company",
    "Role",
    "Fit",
    "Comp",
    "Location",
    "Remote",
    "Notes",
    "Date Found",
    "Date Applied",
    "Date Last Seen",
    "Date Enriched",
    "Link",
    "Source",
]


def _write_legacy(csv_path: Path, rows: list[dict[str, str]]) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_LEGACY_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _legacy_row(url: str, company: str, last_seen: str) -> dict[str, str]:
    return {
        "Status": "found",
        "Company": company,
        "Role": "SRE",
        "Comp": "$100k",
        "Location": "Remote",
        "Date Found": "2026-06-01",
        "Date Last Seen": last_seen,
        "Link": url,
        "Source": "remoteok",
    }


def test_migrate_renames_in_place_and_appends_date_closed(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_legacy(csv_path, [_legacy_row("https://x/1", "Acme", "2026-06-20")])

    assert migrate_legacy_header(csv_path) is True

    header, rows = read_rows(csv_path)
    assert header == CANONICAL_HEADER
    assert rows[0]["Date Verified"] == "2026-06-20"  # value preserved
    assert rows[0]["Date Closed"] == ""
    assert "Date Last Seen" not in rows[0]


def test_migrate_is_idempotent_and_skips_current_files(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_legacy(csv_path, [_legacy_row("https://x/1", "Acme", "2026-06-20")])
    assert migrate_legacy_header(csv_path) is True
    before = csv_path.read_bytes()
    assert migrate_legacy_header(csv_path) is False
    assert csv_path.read_bytes() == before
    # Absent file: no-op, nothing created.
    assert migrate_legacy_header(tmp_path / "missing.csv") is False
    assert not (tmp_path / "missing.csv").exists()


def test_migrate_preserves_user_extra_columns(tmp_path: Path) -> None:
    """A hand-added column survives the migration rewrite in place."""
    header = _LEGACY_HEADER + ["Priority"]
    csv_path = tmp_path / "jobs.csv"
    row = _legacy_row("https://x/1", "Acme", "2026-06-20")
    row["Priority"] = "high"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([row])

    assert migrate_legacy_header(csv_path) is True

    got_header, rows = read_rows(csv_path)
    assert "Priority" in got_header
    assert rows[0]["Priority"] == "high"
    assert rows[0]["Date Verified"] == "2026-06-20"


def test_migrate_merges_both_columns_and_drops_legacy(tmp_path: Path) -> None:
    """A hand-edited file with BOTH date columns: Date Verified wins where
    set, the legacy value fills gaps, and the stray column is dropped -- so
    the read-side legacy fallback can never resurrect a stale date."""
    header = [
        ("Date Verified" if c == "Date Last Seen" else c) for c in _LEGACY_HEADER
    ] + ["Date Last Seen"]
    kept = _legacy_row("https://x/1", "Kept", "2025-12-01")
    kept["Date Verified"] = "2026-06-25"  # explicit value wins
    filled = _legacy_row("https://x/2", "Filled", "2026-06-10")
    filled["Date Verified"] = ""  # gap -> legacy value fills it
    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([kept, filled])

    assert migrate_legacy_header(csv_path) is True

    got_header, rows = read_rows(csv_path)
    assert "Date Last Seen" not in got_header
    by_company = {r["Company"]: r for r in rows}
    assert by_company["Kept"]["Date Verified"] == "2026-06-25"
    assert by_company["Filled"]["Date Verified"] == "2026-06-10"


def test_migrate_takes_backup_before_rewrite(tmp_path: Path) -> None:
    """The one-time structural rewrite snapshots the original first."""
    csv_path = tmp_path / "jobs.csv"
    _write_legacy(csv_path, [_legacy_row("https://x/1", "Acme", "2026-06-20")])
    original = csv_path.read_bytes()

    assert migrate_legacy_header(csv_path) is True

    backups = list((tmp_path / "backups").glob("jobs.csv.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_dry_run_does_not_migrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`jobs run --dry-run` must leave a legacy file byte-identical: a preview
    never writes."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    csv_path = tmp_path / "jobs.csv"
    _write_legacy(csv_path, [_legacy_row("https://x/1", "Acme", "2026-06-20")])
    original = csv_path.read_bytes()

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        return [], [], []

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    from daily_driver.plugins.job_search.config import JobSearchPlugin

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        dry_run=True,
    )
    assert rc == 0
    assert csv_path.read_bytes() == original
    assert not (tmp_path / "backups").exists()


def test_from_csv_row_reads_legacy_column(tmp_path: Path) -> None:
    """Un-migrated rows (e.g. from the archive) still map their last-seen date
    into date_verified via the read-side fallback."""
    job = EnrichedJob.from_csv_row(_legacy_row("https://x/1", "Acme", "2026-06-20"))
    assert job.date_verified is not None
    assert job.date_verified.isoformat() == "2026-06-20"
    assert job.date_closed is None


def test_run_migrates_legacy_file_and_bumps_date_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run over a legacy-header jobs.csv upgrades the file and the re-sight
    freshness bump lands in Date Verified -- the end-to-end migration path."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    csv_path = tmp_path / "jobs.csv"
    _write_legacy(csv_path, [_legacy_row("https://x/1", "Acme", "2026-06-20")])

    # The scrape re-returns the known job -> re-sighting bumps Date Verified.
    reseen = [
        {
            "url": "https://x/1",
            "company": "Acme",
            "role": "SRE",
            "source": "remoteok",
            "location": "Remote",
            "comp": "$100k",
            "date_found": "2026-06-01",
        }
    ]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", reseen)
        return reseen, [], [("remoteok", reseen)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    from daily_driver.plugins.job_search.config import JobSearchPlugin

    rc = runner.run(
        JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True},
                "locations": {"countries": ["US"], "remote": True},
            }
        ),
        tmp_path,
        tmp_path,
        no_enrich=True,
    )
    assert rc == 0

    import datetime as dt

    header, rows = read_rows(csv_path)
    assert header == CANONICAL_HEADER
    (row,) = rows
    assert row["Date Verified"] == dt.date.today().isoformat()
    assert row["Date Closed"] == ""
    assert "Date Last Seen" not in header


def test_prune_migrates_live_and_archive_files(tmp_path: Path) -> None:
    """prune upgrades both jobs.csv and jobs.archive.csv before classifying,
    and _is_stale ages from the migrated Date Verified value."""
    import datetime as dt

    from daily_driver.plugins.job_search.jobs_archive import prune

    csv_path = tmp_path / "jobs.csv"
    stale = _legacy_row("https://x/1", "Stale", "2026-01-05")
    stale["Status"] = "dropped"
    fresh = _legacy_row("https://x/2", "Fresh", "2026-07-01")
    _write_legacy(csv_path, [stale, fresh])
    archive_path = tmp_path / "jobs.archive.csv"
    _write_legacy(
        archive_path, [_legacy_row("https://old/9", "Archived", "2025-12-01")]
    )

    candidates, archived = prune(
        csv_path, tmp_path, cutoff=dt.date(2026, 6, 1), dry_run=False
    )

    assert [c["Company"] for c in candidates] == ["Stale"]
    assert archived == 1
    live_header, live_rows = read_rows(csv_path)
    assert live_header == CANONICAL_HEADER
    assert [r["Company"] for r in live_rows] == ["Fresh"]
    arch_header, arch_rows = read_rows(archive_path)
    assert "Date Verified" in arch_header and "Date Last Seen" not in arch_header
    assert {r["Company"] for r in arch_rows} == {"Archived", "Stale"}
