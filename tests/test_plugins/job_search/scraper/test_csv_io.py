"""Tests for csv_io: backfill budget sentinel and column-order migration."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from daily_driver.plugins.job_search.scraper.csv_io import (
    CANONICAL_HEADER,
    _migrate_legacy_header,
    backfill,
)

# ---------------------------------------------------------------------------
# Item 5 — backfill must not pass sys.maxsize as budget
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG: dict[str, Any] = {
    "job_search": {
        "scraper": {
            "enabled": True,
            "timeout": 5,
            "max_retries": 1,
            "max_enrich_companies": 10,
            "detail_delay_seconds": 0,
        }
    }
}


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
        patch("daily_driver.plugins.job_search.scraper.runner.validate_config"),
    ):
        backfill(_MINIMAL_CONFIG, csv_path)

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
        patch("daily_driver.plugins.job_search.scraper.runner.validate_config"),
    ):
        backfill(_MINIMAL_CONFIG, csv_path)

    mock_company.assert_not_called()
    mock_fit.assert_not_called()


# ---------------------------------------------------------------------------


def test_canonical_header_scan_friendly_order() -> None:
    """First five columns must be the scan-friendly identity + decision columns."""
    assert CANONICAL_HEADER[:5] == ["Status", "Company", "Role", "Fit", "Comp"]


def test_legacy_column_order_migrated_to_new(tmp_path: Path) -> None:
    """_migrate_legacy_header rewrites a legacy-ordered CSV to CANONICAL_HEADER order."""
    old_header = [
        "Status",
        "Notes",
        "Company",
        "Location",
        "Role",
        "Fit",
        "Comp",
        "Date Found",
        "Date Last Seen",
        "Date Applied",
        "Link",
        "Product/Purpose",
        "GD Rating",
        "Source",
    ]
    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=old_header, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerow(
            {
                "Status": "found",
                "Notes": "good fit",
                "Company": "Acme",
                "Location": "Remote",
                "Role": "SRE",
                "Fit": "9",
                "Comp": "$200k",
                "Date Found": "2026-01-01",
                "Date Last Seen": "2026-01-02",
                "Date Applied": "2026-01-05",
                "Link": "https://example.com/j",
                "Product/Purpose": "DevOps SaaS",
                "GD Rating": "4.2",
                "Source": "remoteok",
            }
        )

    result_header = _migrate_legacy_header(csv_path, old_header)

    assert (
        result_header == CANONICAL_HEADER
    ), "returned header does not match CANONICAL_HEADER"

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        file_header = list(reader.fieldnames or [])
        rows = list(reader)

    assert (
        file_header == CANONICAL_HEADER
    ), "on-disk header does not match CANONICAL_HEADER after migration"
    assert len(rows) == 1
    row = rows[0]
    assert row["Company"] == "Acme"
    assert row["Role"] == "SRE"
    assert row["Fit"] == "9"
    assert row["Notes"] == "good fit"
    assert row["Link"] == "https://example.com/j"
    assert row["Product/Purpose"] == "DevOps SaaS"
    assert row["GD Rating"] == "4.2"
    assert row["Source"] == "remoteok"
    assert row["Date Found"] == "2026-01-01"
    assert row["Date Applied"] == "2026-01-05"


def test_migrate_rewrites_archived_status_to_dropped(tmp_path: Path) -> None:
    """Rows carrying the legacy Status='archived' value get rewritten to 'dropped'."""
    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=CANONICAL_HEADER, quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        for status in ("archived", "found", "archived"):
            row = {col: "" for col in CANONICAL_HEADER}
            row["Status"] = status
            row["Company"] = "Acme"
            row["Role"] = "SRE"
            row["Link"] = f"https://example.com/{status}"
            row["Source"] = "remoteok"
            row["Date Found"] = "2026-01-01"
            row["Date Last Seen"] = "2026-01-01"
            writer.writerow(row)

    result = _migrate_legacy_header(csv_path, list(CANONICAL_HEADER))
    assert result == CANONICAL_HEADER

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    statuses = [r["Status"] for r in rows]
    assert statuses == ["dropped", "found", "dropped"]

    # Backup written under output_dir/backups/ with a UTC-ISO stamp.
    backups = list((tmp_path / "backups").glob("jobs.csv.bak.*"))
    assert len(backups) == 1


def test_backup_path_uses_utc_iso_stamp(tmp_path: Path) -> None:
    """Backups must live under backups/ with the YYYY-MM-DDTHH-MM-SSZ stamp."""
    import re

    csv_path = tmp_path / "jobs.csv"
    _write_minimal_csv(csv_path)
    # Force a backup by including a row that triggers the status migration.
    rows = list(csv.DictReader(open(csv_path, newline="", encoding="utf-8")))
    rows[0]["Status"] = "archived"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=CANONICAL_HEADER, quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(rows)

    _migrate_legacy_header(csv_path, list(CANONICAL_HEADER))

    backups = list((tmp_path / "backups").glob("jobs.csv.bak.*"))
    assert len(backups) == 1
    assert re.match(
        r"^jobs\.csv\.bak\.\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}Z$",
        backups[0].name,
    ), backups[0].name


def test_migrate_legacy_header_idempotent(tmp_path: Path) -> None:
    """If header already matches CANONICAL_HEADER, no backup is created and data is unchanged."""
    csv_path = tmp_path / "jobs.csv"
    _write_minimal_csv(csv_path)

    original_mtime = csv_path.stat().st_mtime
    result = _migrate_legacy_header(csv_path, list(CANONICAL_HEADER))

    assert result == CANONICAL_HEADER
    assert csv_path.stat().st_mtime == pytest.approx(original_mtime, abs=0.01)
    bak_files = list(tmp_path.glob("*.bak.*"))
    assert (
        bak_files == []
    ), "no backup should be written when header is already canonical"
