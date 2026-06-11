"""Tests for the typed CSV writer (K6)."""

from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path

import pytest

from daily_driver.plugins.job_search.scraper.csv_io import append_jobs_typed
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
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
    # with_updates routes through model_validate so the status validator
    # normalizes (model_copy would bypass it).
    return base.with_updates(**overrides)


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

    j = _enriched(status="skipped", skip_reason="manually skipped")
    append_jobs_typed(csv_path, [j], CANONICAL_HEADER)

    row = _read_rows(csv_path)[0]
    assert row["Status"] == "skipped"
    assert "manually skipped" in row["Notes"]


def test_append_jobs_typed_normalizes_underscore_status(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(",".join(CANONICAL_HEADER) + "\n", encoding="utf-8")

    j = _enriched(status="ruled_out")
    append_jobs_typed(csv_path, [j], CANONICAL_HEADER)

    assert _read_rows(csv_path)[0]["Status"] == "ruled-out"


def test_append_jobs_typed_warns_once_on_unknown_status(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(",".join(CANONICAL_HEADER) + "\n", encoding="utf-8")

    jobs = [_enriched(status="frobnicated"), _enriched(status="frobnicated")]
    logger_name = "daily_driver.plugins.job_search.scraper.csv_io"
    with caplog.at_level(logging.WARNING, logger=logger_name):
        append_jobs_typed(csv_path, jobs, CANONICAL_HEADER)

    messages = {
        r.getMessage()
        for r in caplog.records
        if r.name == logger_name and r.levelno >= logging.WARNING
    }
    # One distinct warning message regardless of how many rows carried the
    # offending status (the helper dedups offending values into one record).
    assert len(messages) == 1
    assert "frobnicated" in next(iter(messages))


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
