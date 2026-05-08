"""Tests for the typed CSV writer (K6)."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from daily_driver.scraper._impl import append_jobs, append_jobs_typed
from daily_driver.scraper.models import (
    Comp,
    EnrichedJob,
    JobStatus,
    NormalizedJob,
    RawScrapedJob,
)

CANONICAL_HEADER = list(EnrichedJob.CANONICAL_HEADER)


def _enriched(**overrides: object) -> EnrichedJob:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="remoteok",
        location="Remote",
        comp_display="$150,000-$200,000",
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    return base.model_copy(update=dict(overrides))


def _read_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_append_jobs_typed_writes_canonical_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(",".join(CANONICAL_HEADER) + "\n", encoding="utf-8")

    j = _enriched(fit=8, notes="strong match")
    n = append_jobs_typed(csv_path, [j], CANONICAL_HEADER)
    assert n == 1

    rows = _read_rows(csv_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["Company"] == "Acme"
    assert row["Role"] == "SRE"
    assert row["Link"] == "https://example.com/j"
    assert row["Fit"] == "8"
    assert row["Notes"] == "strong match"
    assert row["Status"] == "found"
    assert row["Date Found"] == j.date_found.isoformat()


def test_append_jobs_typed_handles_skipped_with_reason(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(",".join(CANONICAL_HEADER) + "\n", encoding="utf-8")

    j = _enriched(status=JobStatus.SKIPPED, skip_reason="below comp threshold")
    append_jobs_typed(csv_path, [j], CANONICAL_HEADER)

    row = _read_rows(csv_path)[0]
    assert row["Status"] == "skipped"
    assert "below comp threshold" in row["Notes"]


def test_append_jobs_typed_round_trips_via_from_csv_row(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(",".join(CANONICAL_HEADER) + "\n", encoding="utf-8")

    j = _enriched(
        fit=7,
        notes="hi",
        date_applied=dt.date(2026, 5, 1),
    )
    append_jobs_typed(csv_path, [j], CANONICAL_HEADER)

    row = _read_rows(csv_path)[0]
    j2 = EnrichedJob.from_csv_row(row)
    assert j2.company == j.company
    assert j2.role == j.role
    assert j2.fit == j.fit
    assert j2.notes == j.notes
    assert j2.url == j.url
    assert j2.date_applied == j.date_applied


def test_append_jobs_typed_empty_returns_zero(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(",".join(CANONICAL_HEADER) + "\n", encoding="utf-8")
    assert append_jobs_typed(csv_path, [], CANONICAL_HEADER) == 0


def test_typed_and_legacy_writers_produce_same_columns(tmp_path: Path) -> None:
    """Sanity: both writers fill the same canonical columns."""
    csv_path1 = tmp_path / "typed.csv"
    csv_path2 = tmp_path / "dict.csv"
    for p in (csv_path1, csv_path2):
        p.write_text(",".join(CANONICAL_HEADER) + "\n", encoding="utf-8")

    j = _enriched(fit=6, notes="x")
    append_jobs_typed(csv_path1, [j], CANONICAL_HEADER)
    legacy_dict = {
        "company": "Acme",
        "role": "SRE",
        "url": "https://example.com/j",
        "source": "remoteok",
        "location": "Remote",
        "comp": str(Comp.parse("$150,000-$200,000")),
        "fit": 6,
        "notes": "x",
        "status": "found",
        "date_found": j.date_found.isoformat(),
    }
    append_jobs(csv_path2, [legacy_dict], CANONICAL_HEADER)

    typed_row = _read_rows(csv_path1)[0]
    legacy_row = _read_rows(csv_path2)[0]
    for col in ("Company", "Role", "Link", "Source", "Location", "Fit", "Notes"):
        assert typed_row[col] == legacy_row[col], col
