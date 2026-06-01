"""Tests for the typed JobSpy boundary (K2)."""

from __future__ import annotations

import datetime as dt

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.models import RawScrapedJob
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources.jobspy import (
    _comp_from_description,
    jobspy_row_to_raw,
    normalize_jobspy_row,
    scrape_jobspy,
    scrape_jobspy_google,
    scrape_jobspy_indeed,
    scrape_jobspy_linkedin,
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

    def test_comp_recovered_from_description_when_structured_absent(self) -> None:
        # No structured comp from JobSpy -> fall back to the description. Currency
        # is not classified; the recovered figure shows its source symbol.
        out = normalize_jobspy_row(
            _row(
                min_amount=None,
                max_amount=None,
                description="Compensation: $120,000 - $140,000 per year.",
            )
        )
        assert out["comp"] == "$120,000–140,000/yr"

    def test_structured_comp_wins_over_description(self) -> None:
        # When JobSpy supplies structured comp, the description is not consulted
        # even if it also contains a salary.
        out = normalize_jobspy_row(_row(description="Pay: $10 - $20 per hour"))
        assert out["comp"].startswith("$150,000")


class TestCompFromDescription:
    def test_yearly_range(self) -> None:
        comp = _comp_from_description("Range: $150,000 - $200,000 / year")
        assert comp == "$150,000–200,000/yr"

    def test_hourly_is_annualized_not_raw(self) -> None:
        # Regression guard: hourly figures are annualized so the Comp column
        # reads as a comparable yearly amount.
        comp = _comp_from_description("Pay range $50 - $70 per hour")
        assert comp == "$104,000–145,600/yr"
        assert "/hr" not in comp

    def test_no_salary_returns_empty(self) -> None:
        assert _comp_from_description("Competitive compensation offered.") == ""


@pytest.mark.parametrize("bad_url", ["  https://x  ", "https://x"])
def test_url_stripped_at_boundary(bad_url: str) -> None:
    raw = jobspy_row_to_raw(_row(job_url=bad_url))
    assert raw is not None
    assert raw.url == "https://x"


class TestPerSiteScrapers:
    """The thin per-site wrappers forward the correct ``site_name`` list."""

    @staticmethod
    def _config() -> ScrapeContext:
        return ScrapeContext(
            plugin=JobSearchPlugin.model_validate(
                {
                    "roles": ["software engineer"],
                    "locations": {"countries": ["US"]},
                    "scraper": {
                        "enabled": True,
                        "search_terms": ["software engineer"],
                    },
                }
            )
        )

    @staticmethod
    def _install_mock(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
        import pandas as pd

        calls: dict[str, object] = {}

        def fake(**kwargs: object) -> pd.DataFrame:
            calls["site_name"] = kwargs.get("site_name")
            return pd.DataFrame()

        import jobspy as jobspy_pkg

        monkeypatch.setattr(jobspy_pkg, "scrape_jobs", fake)
        return calls

    def test_scrape_jobspy_linkedin_calls_correct_site(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._install_mock(monkeypatch)
        scrape_jobspy_linkedin(self._config())
        assert calls["site_name"] == ["linkedin"]

    def test_scrape_jobspy_indeed_calls_correct_site(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._install_mock(monkeypatch)
        scrape_jobspy_indeed(self._config())
        assert calls["site_name"] == ["indeed"]

    def test_scrape_jobspy_google_calls_correct_site(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._install_mock(monkeypatch)
        scrape_jobspy_google(self._config())
        assert calls["site_name"] == ["google"]

    def test_scrape_jobspy_sites_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._install_mock(monkeypatch)
        scrape_jobspy(self._config(), sites=["indeed"])
        assert calls["site_name"] == ["indeed"]

    def test_scrape_jobspy_default_uses_all_three(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._install_mock(monkeypatch)
        scrape_jobspy(self._config())
        assert calls["site_name"] == ["linkedin", "indeed", "google"]
