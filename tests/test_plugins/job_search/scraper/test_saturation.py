"""Saturation detect-and-report: a query that returns its full cap was
truncated -- coverage is incomplete and the run must say so, not look clean.

Plan phase 2. Detection lives inside the sources (the runner only sees the
flat merged list): jobspy flags a (term x country) query whose PRE-role-filter
row count hits ``results_wanted_per_query`` ("cap"), and LinkedIn's ~100-row
per-IP wall when a larger request plateaus ("plateau"). Records land on
``ScrapeContext.saturation``; the runner renders a Scraping-section line and a
``saturated_queries`` manifest key. Detection only -- no scrape behavior
change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.models import SaturationRecord
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources.jobspy import scrape_jobspy
from tests.test_plugins.job_search.scraper.test_run_resilience import (
    _scraped,
    _us_remote_plugin,
)


def _frame(site: str, n: int) -> Any:
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "site": site,
                "title": f"SRE {i}",
                "company": f"Co{i}",
                "job_url": f"https://{site}.example/{i}",
                "location": "Remote, US",
            }
            for i in range(n)
        ]
    )


def _ctx(results_wanted: int, sites: dict[str, Any] | None = None) -> ScrapeContext:
    cfg: dict[str, Any] = {
        "roles": ["sre"],
        "locations": {"countries": ["US"]},
        "scraper": {"enabled": True, "search_terms": ["sre"]},
        "sources": sites
        or {"linkedin": {"enabled": True, "results_wanted_per_query": results_wanted}},
    }
    return ScrapeContext(plugin=JobSearchPlugin.model_validate(cfg))


def _install_fake(monkeypatch: pytest.MonkeyPatch, frame: Any) -> None:
    import jobspy as jobspy_pkg

    monkeypatch.setattr(jobspy_pkg, "scrape_jobs", lambda **kw: frame)


def test_query_at_cap_records_saturation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly results_wanted rows back -> the query was truncated."""
    ctx = _ctx(results_wanted=10)
    _install_fake(monkeypatch, _frame("linkedin", 10))

    scrape_jobspy(ctx, sites=["linkedin"])

    (rec,) = ctx.saturation
    assert isinstance(rec, SaturationRecord)
    assert rec.source == "linkedin"
    assert rec.returned == 10
    assert rec.requested == 10
    assert rec.kind == "cap"
    assert "sre" in rec.query and "US" in rec.query


def test_query_under_cap_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(results_wanted=10)
    _install_fake(monkeypatch, _frame("linkedin", 4))

    scrape_jobspy(ctx, sites=["linkedin"])

    assert ctx.saturation == []


def test_linkedin_plateau_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for more than ~100 from LinkedIn and getting ~100 back is the
    per-IP rate-limit wall, not exhaustion -- flagged as 'plateau'."""
    ctx = _ctx(results_wanted=200)
    _install_fake(monkeypatch, _frame("linkedin", 100))

    scrape_jobspy(ctx, sites=["linkedin"])

    (rec,) = ctx.saturation
    assert rec.kind == "plateau"
    assert rec.returned == 100
    assert rec.requested == 200


def test_merged_call_attributes_per_site(monkeypatch: pytest.MonkeyPatch) -> None:
    """In a two-site merged frame only the site that hit its cap is flagged."""
    import pandas as pd

    ctx = _ctx(
        results_wanted=10,
        sites={
            "linkedin": {"enabled": True, "results_wanted_per_query": 10},
            "indeed": {"enabled": True},
        },
    )
    merged = pd.concat([_frame("linkedin", 10), _frame("indeed", 3)])
    _install_fake(monkeypatch, merged)

    scrape_jobspy(ctx, sites=["linkedin", "indeed"])

    assert [(r.source, r.kind) for r in ctx.saturation] == [("linkedin", "cap")]


def test_workday_page_ceiling_records_saturation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workday board that exhausts _MAX_PAGES without covering its total is
    flagged: the run saw only part of the board's inventory."""
    from unittest.mock import MagicMock

    from daily_driver.plugins.job_search.scraper.sources import (
        workday as workday_module,
    )

    monkeypatch.setattr(workday_module, "_MAX_PAGES", 2)

    def fake_post(session: Any, url: str, ctx: Any, *, json: Any, **kw: Any) -> Any:
        resp = MagicMock()
        resp.json.return_value = {
            "total": 1000,
            "jobPostings": [
                {
                    "title": f"SRE {json['offset'] + i}",
                    "externalPath": f"/job/x/SRE_{json['offset'] + i}",
                }
                for i in range(20)
            ],
        }
        return resp

    monkeypatch.setattr(workday_module, "_api_post", fake_post)
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": ["sre"],
                "scraper": {"enabled": True},
                "sources": {
                    "workday": {
                        "enabled": True,
                        "workday_boards": [
                            {"tenant": "acme", "host": "wd5", "site": "careers"}
                        ],
                    }
                },
            }
        )
    )
    workday_module.scrape_workday(ctx)

    (rec,) = ctx.saturation
    assert rec.source == "workday"
    assert rec.query == "acme"
    assert rec.returned == 40  # 2 pages x 20
    assert rec.requested == 1000  # the board's advertised total
    assert rec.kind == "cap"


def test_run_reports_saturation_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saturation records collected during the scrape land in the manifest as
    ``saturated_queries`` -- on the happy path and with an empty default."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    jobs = [_scraped("https://x/1", "Acme")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        ctx.saturation.append(
            SaturationRecord(
                source="linkedin",
                query="sre x US",
                returned=50,
                requested=50,
                kind="cap",
            )
        )
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)
    assert rc == 0

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["saturated_queries"] == [
        {
            "source": "linkedin",
            "query": "sre x US",
            "returned": 50,
            "requested": 50,
            "kind": "cap",
        }
    ]


def test_run_manifest_saturation_empty_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    jobs = [_scraped("https://x/1", "Acme")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)
    assert rc == 0

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["saturated_queries"] == []
