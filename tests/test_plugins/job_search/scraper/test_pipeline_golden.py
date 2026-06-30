"""Golden behavior pins for the scraper CSV pipeline (audit H-2 collapse).

These tests fix the externally-observable contract of the scraper's CSV path
BEFORE the dual-representation collapse, and must survive the refactor
unchanged:

- The exact 13-column ``jobs.csv`` header, byte-for-byte (Remote added after
  Location in task #6; GD Rating and Product/Purpose removed with the
  company-info pass).
- A full-coverage EnrichedJob round-trip: write -> read -> rewrite is stable,
  including unicode, commas, quotes, and newlines in free-text fields.
- A backfill round-trip on a fixture CSV: rows survive a no-op backfill
  (enrichment stubbed out) byte-identically, and the header/column order holds.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.csv_io import (
    CANONICAL_HEADER,
    append_jobs_typed,
)
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    NormalizedJob,
    RawScrapedJob,
)

# The frozen 13-column jobs.csv layout. The single derived CANONICAL_HEADER must
# equal this list. Remote sits immediately after Location. Reads are
# header-name-based, so files stored in an older column order (or with the
# removed GD Rating / Product/Purpose columns) still load and adopt this order on
# the next rewrite.
_EXPECTED_HEADER = [
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
    "Link",
    "Source",
]


def test_canonical_header_is_exactly_the_frozen_13_columns() -> None:
    assert CANONICAL_HEADER == _EXPECTED_HEADER


def _coverage_jobs() -> list[EnrichedJob]:
    """EnrichedJobs exercising every field plus CSV-hostile free text."""
    raw = RawScrapedJob(
        company="Acmé, Inc.",  # unicode + comma
        role='Senior "SRE"',  # embedded quotes
        url="https://example.com/job/1",
        source="remoteok",
        location="Remote — Worldwide",  # em-dash unicode
        comp_display="$150,000-$200,000",  # comma in comp
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    j1 = base.model_copy(
        update={
            "fit": 7,
            "notes": "Strong match\nmultiline, with comma",  # newline + comma
            "date_found": dt.date(2026, 1, 2),
            "date_applied": dt.date(2026, 5, 1),
            "posted_date": dt.date(2026, 1, 1),
            "description_text": "long desc",
        }
    )

    raw2 = RawScrapedJob(
        company="Béta Çorp",
        role="Platform Engineer",
        url="https://example.com/job/2",
        source="Greenhouse (beta-corp)",
    )
    j2 = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw2)).model_copy(
        update={
            "status": "skipped",
            "skip_reason": "manual skip, with comma",
        }
    )
    return [j1, j2]


def _write(path: Path, jobs: list[EnrichedJob]) -> None:
    path.write_text(",".join(CANONICAL_HEADER) + "\n", encoding="utf-8")
    append_jobs_typed(path, jobs, CANONICAL_HEADER)


def test_golden_round_trip_is_byte_stable(tmp_path: Path) -> None:
    """Write -> read -> rewrite reproduces the same bytes for every field."""
    first = tmp_path / "first.csv"
    _write(first, _coverage_jobs())
    first_bytes = first.read_bytes()

    with open(first, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    reread = [EnrichedJob.from_csv_row(r) for r in rows]

    second = tmp_path / "second.csv"
    _write(second, reread)

    assert second.read_bytes() == first_bytes


def _backfill_plugin() -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
            "enrichment": {
                "max_enrich_fit": 0,
                "detail_delay_seconds": 0,
            },
        }
    )


def _stub_enrichment(monkeypatch: pytest.MonkeyPatch) -> None:
    # No-op detail + LLM enrichment: backfill still runs its read -> rewrite cycle.
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_detail(jobs: list[Any], ctx: Any, *, progress: Any = None) -> Any:
        if progress is not None:
            progress(len(jobs))
        return jobs, {
            "total": len(jobs),
            "fetched": 0,
            "enriched": 0,
            "failed": 0,
            "skipped": len(jobs),
            "skip_reasons": {},
        }

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        return (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", fake_detail)
    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)


def test_backfill_round_trip_preserves_rows_and_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-op backfill (enrichment stubbed) leaves rows + header order intact."""
    csv_path = tmp_path / "jobs.csv"
    # A fixture row that already needs enrichment (blank Fit/Notes) so backfill
    # does not early-return, plus a fully-filled row.
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CANONICAL_HEADER, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        needs = {c: "" for c in CANONICAL_HEADER}
        needs.update(
            {
                "Status": "found",
                "Company": "Acmé, Inc.",
                "Role": 'Senior "SRE"',
                "Comp": "$150,000-$200,000",
                "Location": "Remote",
                "Notes": "",
                "Date Found": "2026-01-02",
                "Date Last Seen": "2026-01-02",
                "Link": "https://example.com/job/1",
                "Source": "remoteok",
            }
        )
        w.writerow(needs)

    header_before = csv_path.read_text(encoding="utf-8").splitlines()[0]
    links_before = {
        r["Link"]
        for r in csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines())
    }

    _stub_enrichment(monkeypatch)
    runner.run_backfill(_backfill_plugin(), csv_path, tmp_path)

    text_after = csv_path.read_text(encoding="utf-8")
    header_after = text_after.splitlines()[0]
    rows_after = list(csv.DictReader(text_after.splitlines()))
    links_after = {r["Link"] for r in rows_after}

    assert header_after == header_before
    assert links_after == links_before
    # Free-text fields with unicode/quotes survive the round-trip.
    target = next(r for r in rows_after if r["Link"] == "https://example.com/job/1")
    assert target["Company"] == "Acmé, Inc."
    assert target["Role"] == 'Senior "SRE"'
    assert target["Comp"] == "$150,000-$200,000"
