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


class TestMergedJobspyScraper:
    """``scrape_jobspy(ctx, sites=[...])`` requests the given sites in one
    ``scrape_jobs`` call, reading per-site query knobs off the top-level
    ``linkedin`` / ``indeed`` source toggles. The runner always hands a
    single-site list (one row per enabled site); the adapter still accepts a
    multi-site list, which these tests exercise directly."""

    @staticmethod
    def _config(countries: list[str] | None = None, **sources: object) -> ScrapeContext:
        cfg: dict[str, object] = {
            "roles": ["software engineer"],
            "locations": {"countries": countries or ["US"]},
            "scraper": {
                "enabled": True,
                "search_terms": ["software engineer"],
            },
        }
        # Default both sites enabled when the test does not specify otherwise.
        cfg["sources"] = sources or {"linkedin": True, "indeed": True}
        return ScrapeContext(plugin=JobSearchPlugin.model_validate(cfg))

    @staticmethod
    def _install_mock(
        monkeypatch: pytest.MonkeyPatch, frame: object | None = None
    ) -> dict[str, object]:
        import pandas as pd

        calls: dict[str, object] = {}

        def fake(**kwargs: object) -> object:
            calls.update(kwargs)
            return frame if frame is not None else pd.DataFrame()

        import jobspy as jobspy_pkg

        monkeypatch.setattr(jobspy_pkg, "scrape_jobs", fake)
        return calls

    def test_default_requests_both_sites_in_one_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._install_mock(monkeypatch)
        scrape_jobspy(self._config(), sites=["linkedin", "indeed"])
        assert calls["site_name"] == ["linkedin", "indeed"]

    def test_indeed_country_knob_used_as_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The indeed `country` knob is the fallback for ISO codes JobSpy's enum
        # lacks; "XX" is not a JobSpy country, so the configured value is sent.
        calls = self._install_mock(monkeypatch)
        scrape_jobspy(
            self._config(
                countries=["XX"], indeed={"enabled": True, "country": "Canada"}
            ),
            sites=["indeed"],
        )
        assert calls["site_name"] == ["indeed"]
        assert calls["country_indeed"] == "Canada"

    def test_single_site_passes_only_that_site(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._install_mock(monkeypatch)
        scrape_jobspy(self._config(), sites=["linkedin"])
        assert calls["site_name"] == ["linkedin"]

    def test_results_wanted_and_hours_old_knobs_reach_scrape_jobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """results_wanted_per_query / hours_old propagate to the scrape_jobs call.

        A single enabled site keeps the per-site toggle unambiguous.
        """
        calls = self._install_mock(monkeypatch)
        scrape_jobspy(
            self._config(
                linkedin={
                    "enabled": True,
                    "results_wanted_per_query": 17,
                    "hours_old": 72,
                }
            ),
            sites=["linkedin"],
        )
        assert calls["results_wanted"] == 17
        assert calls["hours_old"] == 72

    def test_search_terms_override_reaches_scrape_jobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """scraper.search_terms overrides the query term sent to scrape_jobs."""
        calls = self._install_mock(monkeypatch)
        cfg = ScrapeContext(
            plugin=JobSearchPlugin.model_validate(
                {
                    "roles": ["ignored role"],
                    "locations": {"countries": ["US"]},
                    "scraper": {
                        "enabled": True,
                        "search_terms": ["platform engineer"],
                    },
                    "sources": {"linkedin": True},
                }
            )
        )
        scrape_jobspy(cfg, sites=["linkedin"])
        assert calls["search_term"] == "platform engineer"

    def test_empty_sites_skips_the_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._install_mock(monkeypatch)
        out = scrape_jobspy(self._config(), sites=[])
        assert out == []
        assert calls == {}  # scrape_jobs never invoked

    @staticmethod
    def _row(site: str, url: str) -> dict[str, object]:
        return {
            "company": "Acme",
            "title": "Software Engineer",
            "location": "Remote",
            "job_url": url,
            "site": site,
            "description": "infra",
            "min_amount": None,
            "max_amount": None,
            "currency": None,
            "interval": None,
        }

    def test_merged_call_raise_retries_each_site_and_keeps_good_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The installed jobspy has no per-site isolation: one site raising kills
        the merged DataFrame. Our wrapper must retry each enabled site alone so
        the healthy site's rows survive (here: linkedin raises, indeed recovers)."""
        import jobspy as jobspy_pkg
        import pandas as pd

        def fake(**kwargs: object) -> object:
            site_name = kwargs["site_name"]
            assert isinstance(site_name, list)
            if site_name == ["linkedin", "indeed"]:
                raise RuntimeError("linkedin blew up the merged call")
            if site_name == ["linkedin"]:
                raise RuntimeError("linkedin still down")
            if site_name == ["indeed"]:
                return pd.DataFrame([self._row("indeed", "https://indeed.com/job/1")])
            return pd.DataFrame()

        monkeypatch.setattr(jobspy_pkg, "scrape_jobs", fake)
        out = scrape_jobspy(self._config())
        assert len(out) == 1
        assert out[0]["source"] == "indeed"  # healthy site's rows kept

    def test_zero_row_enabled_site_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An enabled site that contributes zero rows all run must warn (the merge
        otherwise hides a single-board outage behind the other site's results)."""
        import logging

        import pandas as pd

        # Merged frame carries only indeed rows; linkedin contributes nothing.
        frame = pd.DataFrame([self._row("indeed", "https://indeed.com/job/1")])
        self._install_mock(monkeypatch, frame=frame)
        with caplog.at_level(
            logging.WARNING,
            logger="daily_driver.plugins.job_search.scraper.sources.jobspy",
        ):
            scrape_jobspy(self._config())
        warns = {
            r.getMessage()
            for r in caplog.records
            if "returned 0 rows across all searches" in r.getMessage()
        }
        assert any("linkedin" in m for m in warns), warns
        assert not any("indeed" in m for m in warns), "indeed contributed, no warn"

    def test_reports_progress_per_term_country(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each (term x country) pair advances the live progress reporter."""
        from dataclasses import replace

        self._install_mock(monkeypatch)
        reports: list[tuple[int, int | None]] = []
        ctx = replace(
            self._config(), report=lambda done, total: reports.append((done, total))
        )
        scrape_jobspy(ctx)
        # One config term x one country -> reported once at (0, 1).
        assert reports and reports[0] == (0, 1)
        assert all(total == 1 for _done, total in reports)

    def test_checkpoints_each_term_country_unit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each finished (term x country) unit's matched rows are handed to
        ``ctx.checkpoint`` as the unit completes, so a crash mid-scrape keeps the
        completed units. Two countries -> two checkpoint batches."""
        from dataclasses import replace

        import jobspy as jobspy_pkg
        import pandas as pd

        # A distinct row per country so each unit's batch is identifiable.
        def fake(**kwargs: object) -> object:
            location = kwargs["location"]
            url = f"https://indeed.com/job/{location}"
            return pd.DataFrame([self._row("indeed", url)])

        monkeypatch.setattr(jobspy_pkg, "scrape_jobs", fake)
        batches: list[list[dict]] = []
        ctx = replace(
            self._config(countries=["US", "CA"], indeed=True),
            checkpoint=lambda batch: batches.append(list(batch)),
        )
        out = scrape_jobspy(ctx, sites=["indeed"])
        # One unit per country -> two checkpoint batches, each with that unit's row.
        assert len(batches) == 2
        assert all(len(b) == 1 for b in batches)
        # The full list is still returned for the manifest/results path.
        assert len(out) == 2
