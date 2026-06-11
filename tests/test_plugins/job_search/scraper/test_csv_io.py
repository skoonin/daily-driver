"""Tests for csv_io: backup helper and the model's Fit-cell parsing."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    parse_fit,
    parse_fit_cell,
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


def test_canonical_header_scan_friendly_order() -> None:
    """First five columns must be the scan-friendly identity + decision columns."""
    assert CANONICAL_HEADER[:5] == ["Status", "Company", "Role", "Fit", "Comp"]


def test_parse_fit_bare_int() -> None:
    """A bare int Fit cell parses straight through (W1.1)."""
    assert parse_fit("7") == 7
    assert parse_fit(7) == 7


def test_parse_fit_tolerates_legacy_suffix() -> None:
    """Legacy "7/10" cells must parse to the leading int, not None (W1.1)."""
    assert parse_fit("7/10") == 7


def _row_with_fit(fit: str) -> dict[str, str]:
    return {
        "Status": "found",
        "Company": "Acme",
        "Role": "SRE",
        "Fit": fit,
        "Link": "https://example.com/j",
        "Source": "remoteok",
        "Date Found": "2026-01-01",
    }


def test_from_csv_row_legacy_suffix_fit() -> None:
    """ "7/10" reads as 7 (read-path parity with parse_fit, W1.1/#79)."""
    assert EnrichedJob.from_csv_row(_row_with_fit("7/10")).fit == 7


def test_from_csv_row_out_of_range_fit_clamps_with_warning(caplog: Any) -> None:
    """A legacy/hand-edited "15" must clamp to 10 (never raise) and warn."""
    import logging

    with caplog.at_level(logging.WARNING):
        j = EnrichedJob.from_csv_row(_row_with_fit("15"))
    assert j.fit == 10
    assert any("out of range" in r.getMessage() for r in caplog.records)

    with caplog.at_level(logging.WARNING):
        j0 = EnrichedJob.from_csv_row(_row_with_fit("0"))
    assert j0.fit == 1


def test_from_csv_row_unparseable_fit_warns(caplog: Any) -> None:
    """A non-empty unparseable Fit cell yields None and logs a warning (W1.1)."""
    import logging

    with caplog.at_level(logging.WARNING):
        j = EnrichedJob.from_csv_row(_row_with_fit("high"))
    assert j.fit is None
    assert any(
        "dropping unparseable Fit cell" in r.getMessage() for r in caplog.records
    )


def test_from_csv_row_blank_fit_silent(caplog: Any) -> None:
    """A blank Fit cell yields None with no warning."""
    import logging

    with caplog.at_level(logging.WARNING):
        j = EnrichedJob.from_csv_row(_row_with_fit(""))
    assert j.fit is None
    assert not [r for r in caplog.records if "Fit" in r.getMessage()]


def test_parse_fit_cell_clamp_and_warn(caplog: Any) -> None:
    """parse_fit_cell clamps out-of-range, warns on unparseable, silent on blank."""
    import logging

    assert parse_fit_cell("7/10") == 7
    with caplog.at_level(logging.WARNING):
        assert parse_fit_cell("15", company="Acme") == 10
        assert parse_fit_cell("0", company="Acme") == 1
        assert parse_fit_cell("high", company="Acme") is None
    msgs = [r.getMessage() for r in caplog.records]
    assert any("out of range" in m for m in msgs)
    assert any("dropping unparseable Fit cell" in m for m in msgs)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert parse_fit_cell("") is None
    assert not caplog.records


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
