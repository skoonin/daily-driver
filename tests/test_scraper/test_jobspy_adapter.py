"""Tests for the typed JobSpy boundary (K2)."""

from __future__ import annotations

import datetime as dt

import pytest

from daily_driver.scraper.models import RawScrapedJob
from daily_driver.scraper.sources.jobspy import (
    jobspy_row_to_raw,
    normalize_jobspy_row,
)


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "company": "Acme",
        "title": "SRE",
        "location": "Remote",
        "job_url": "https://example.com/job/1",
        "site": "linkedin",
        "description": "About the role...",
        "min_amount": 150_000,
        "max_amount": 200_000,
        "currency": "USD",
        "interval": "yearly",
    }
    base.update(overrides)
    return base


class TestJobspyRowToRaw:
    def test_happy_path(self) -> None:
        raw = jobspy_row_to_raw(_row())
        assert isinstance(raw, RawScrapedJob)
        assert raw.company == "Acme"
        assert raw.role == "SRE"
        assert raw.url == "https://example.com/job/1"
        assert raw.source == "linkedin"
        assert raw.comp_display.startswith("$150,000")
        assert raw.date_found == dt.date.today()  # noqa: DTZ011

    def test_missing_role_returns_none(self) -> None:
        # JobSpy emits empty titles for ads / non-job cards; reject silently.
        assert jobspy_row_to_raw(_row(title="")) is None
        assert jobspy_row_to_raw(_row(title="   ")) is None

    def test_q15_extra_ignored(self) -> None:
        row = _row()
        row["mystery_future_field"] = "from_jobspy_2027"
        # Must not raise — Q15 says RawScrapedJob.extra='ignore'.
        raw = jobspy_row_to_raw(row)
        assert raw is not None
        assert not hasattr(raw, "mystery_future_field")

    def test_nan_and_missing_handled(self) -> None:
        # JobSpy DataFrames produce NaN floats for missing string cells;
        # _jobspy_str guards against that. Use float("nan") to simulate.
        nan = float("nan")
        raw = jobspy_row_to_raw(
            _row(company=nan, location=nan, description=nan, currency=nan)
        )
        assert raw is not None
        assert raw.company == ""
        assert raw.location == ""

    def test_missing_site_defaults_to_jobspy(self) -> None:
        raw = jobspy_row_to_raw(_row(site=""))
        assert raw is not None
        assert raw.source == "jobspy"

    def test_no_comp_amounts_yields_empty_display(self) -> None:
        raw = jobspy_row_to_raw(_row(min_amount=None, max_amount=None))
        assert raw is not None
        assert raw.comp_display == ""


class TestNormalizeJobspyRow:
    def test_dict_shape_preserved(self) -> None:
        out = normalize_jobspy_row(_row())
        assert out["company"] == "Acme"
        assert out["role"] == "SRE"
        assert out["url"] == "https://example.com/job/1"
        assert out["source"] == "linkedin"
        assert out["description"] == "About the role..."
        assert out["date_found"] == dt.date.today().isoformat()  # noqa: DTZ011
        assert out["comp"].startswith("$150,000")

    def test_empty_role_keeps_legacy_dict_shape(self) -> None:
        out = normalize_jobspy_row(_row(title=""))
        # Legacy callers expect a dict even when role is empty (downstream
        # filters drop it). Return shape must be unchanged from pre-K2.
        assert out["role"] == ""
        assert out["company"] == "Acme"
        assert "description" in out

    def test_description_carried_separately(self) -> None:
        # RawScrapedJob doesn't model description; the legacy dict still must.
        out = normalize_jobspy_row(_row(description="long form"))
        assert out["description"] == "long form"


@pytest.mark.parametrize("bad_url", ["  https://x  ", "https://x"])
def test_url_stripped_at_boundary(bad_url: str) -> None:
    raw = jobspy_row_to_raw(_row(job_url=bad_url))
    assert raw is not None
    assert raw.url == "https://x"
