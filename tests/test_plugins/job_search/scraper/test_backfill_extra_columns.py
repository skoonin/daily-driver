"""Backfill must carry through unknown / hand-added jobs.csv columns (review fix 1).

EnrichedJob knows only the 15 canonical columns. A user who hand-adds a column
(e.g. "Priority") must keep both its header label and every cell value across a
backfill rewrite. The carry-through lives in csv_io, not the model.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER, backfill
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext


def _ctx() -> ScrapeContext:
    # Zero budgets => enrichers no-op (no provider calls); backfill still runs
    # its read -> rewrite cycle.
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
                "enrichment": {
                    "max_enrich_companies": 0,
                    "max_enrich_fit": 0,
                    "detail_delay_seconds": 0,
                },
            }
        )
    )


def _write_with_extra(csv_path: Path) -> list[str]:
    header = CANONICAL_HEADER + ["Priority", "Recruiter"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        # Row 1 needs enrichment (blank Product/Fit/Notes/GD) so backfill runs.
        row = {c: "" for c in header}
        row.update(
            {
                "Status": "found",
                "Company": "Acme",
                "Role": "SRE",
                "Location": "Berlin, Germany",
                "Date Found": "2026-01-02",
                "Date Last Seen": "2026-01-02",
                "Link": "https://example.com/job/1",
                "Source": "remoteok",
                "Priority": "high",
                "Recruiter": "Jane Doe",
            }
        )
        w.writerow(row)
    return header


def _read(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    text = csv_path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    return list(reader.fieldnames or []), list(reader)


def test_backfill_preserves_extra_header_and_cells(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_with_extra(csv_path)

    backfill(_ctx(), csv_path, tmp_path)

    header_after, rows_after = _read(csv_path)
    # Canonical columns first, then the extras in stable original order.
    assert header_after == CANONICAL_HEADER + ["Priority", "Recruiter"]
    assert rows_after[0]["Priority"] == "high"
    assert rows_after[0]["Recruiter"] == "Jane Doe"
    # Canonical data still intact.
    assert rows_after[0]["Company"] == "Acme"
    assert rows_after[0]["Link"] == "https://example.com/job/1"


def test_backfill_logs_carried_columns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_with_extra(csv_path)

    with caplog.at_level(logging.INFO, logger="daily_driver"):
        backfill(_ctx(), csv_path, tmp_path)

    assert any(
        "Priority" in r.getMessage() and "Recruiter" in r.getMessage()
        for r in caplog.records
    ), caplog.records
