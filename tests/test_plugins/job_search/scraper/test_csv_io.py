"""Tests for csv_io: backfill budget sentinel and backup helper."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.csv_io import (
    CANONICAL_HEADER,
    backfill,
)
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

# ---------------------------------------------------------------------------
# Item 5 — backfill must not pass sys.maxsize as budget
# ---------------------------------------------------------------------------

_MINIMAL_CTX = ScrapeContext(
    plugin=JobSearchPlugin.model_validate(
        {
            "scraper": {
                "enabled": True,
                "timeout": 5,
                "max_retries": 1,
            },
            "enrichment": {
                "max_enrich_companies": 10,
                "detail_delay_seconds": 0,
            },
        }
    )
)


def _write_minimal_csv(path: Path, *, needs_enrichment: bool = True) -> None:
    """Write a single-row jobs.csv that requires enrichment (or is fully filled)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=CANONICAL_HEADER, quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        row = {col: "" for col in CANONICAL_HEADER}
        row["Status"] = "found"
        row["Company"] = "Acme"
        row["Role"] = "SRE"
        row["Link"] = "https://example.com/j"
        row["Source"] = "test"
        row["Date Found"] = "2026-01-01"
        row["Date Last Seen"] = "2026-01-01"
        if not needs_enrichment:
            row["Fit"] = "8"
            row["Notes"] = "done"
            row["Product/Purpose"] = "SaaS"
            row["GD Rating"] = "4.0"
        writer.writerow(row)


def test_backfill_uses_config_budget_not_maxsize(tmp_path: Path) -> None:
    """backfill() must pass budget=0 (config sentinel), never sys.maxsize."""
    csv_path = tmp_path / "jobs.csv"
    _write_minimal_csv(csv_path)

    called_with: list[dict] = []

    def _capture_company(*args: Any, **kwargs: Any) -> dict:
        called_with.append({"fn": "enrich_company_descriptions", **kwargs})
        return {}

    def _capture_fit(*args: Any, **kwargs: Any) -> dict:
        called_with.append({"fn": "enrich_fit_and_notes", **kwargs})
        return {}

    with (
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.enrich_company_descriptions",
            side_effect=_capture_company,
        ),
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.enrich_fit_and_notes",
            side_effect=_capture_fit,
        ),
    ):
        backfill(_MINIMAL_CTX, csv_path)

    assert called_with, "enrichment functions were not called at all"
    for call in called_with:
        assert call.get("budget") != sys.maxsize, (
            f"{call['fn']} was called with budget=sys.maxsize; "
            "it should use budget=0 (the config-default sentinel)"
        )


def test_backfill_skips_enrichment_when_all_rows_filled(tmp_path: Path) -> None:
    """backfill() must return early without calling enrichment if all rows are complete."""
    csv_path = tmp_path / "jobs.csv"
    _write_minimal_csv(csv_path, needs_enrichment=False)

    with (
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.enrich_company_descriptions"
        ) as mock_company,
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.enrich_fit_and_notes"
        ) as mock_fit,
    ):
        backfill(_MINIMAL_CTX, csv_path)

    mock_company.assert_not_called()
    mock_fit.assert_not_called()


# ---------------------------------------------------------------------------


def test_canonical_header_scan_friendly_order() -> None:
    """First five columns must be the scan-friendly identity + decision columns."""
    assert CANONICAL_HEADER[:5] == ["Status", "Company", "Role", "Fit", "Comp"]


def test_make_backup_uses_utc_iso_stamp(tmp_path: Path) -> None:
    """backfill backups live under backups/ with the YYYY-MM-DDTHH-MM-SSZ stamp."""
    import re

    from daily_driver.plugins.job_search.scraper.csv_io import _make_backup

    csv_path = tmp_path / "jobs.csv"
    _write_minimal_csv(csv_path)

    backup = _make_backup(csv_path)
    assert backup.parent == tmp_path / "backups"
    assert re.match(
        r"^jobs\.csv\.bak\.\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}Z$",
        backup.name,
    ), backup.name
