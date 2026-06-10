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
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    parse_fit,
    parse_fit_cell,
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

    def _capture_company(jobs: Any, *args: Any, **kwargs: Any) -> Any:
        called_with.append({"fn": "enrich_company_descriptions", **kwargs})
        return jobs, {}

    def _capture_fit(jobs: Any, *args: Any, **kwargs: Any) -> Any:
        called_with.append({"fn": "enrich_fit_and_notes", **kwargs})
        return jobs, {}

    with (
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.llm.enrich_company_descriptions",
            side_effect=_capture_company,
        ),
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.llm.enrich_fit_and_notes",
            side_effect=_capture_fit,
        ),
    ):
        backfill(_MINIMAL_CTX, csv_path, tmp_path)

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
            "daily_driver.plugins.job_search.scraper.enrichment.llm.enrich_company_descriptions"
        ) as mock_company,
        patch(
            "daily_driver.plugins.job_search.scraper.enrichment.llm.enrich_fit_and_notes"
        ) as mock_fit,
    ):
        backfill(_MINIMAL_CTX, csv_path, tmp_path)

    mock_company.assert_not_called()
    mock_fit.assert_not_called()


# ---------------------------------------------------------------------------


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
