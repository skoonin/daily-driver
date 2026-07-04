"""``run(no_enrich=True)`` skips all enrichment and appends unenriched rows.

The flag must short-circuit every enrichment phase: zero detail/fit
enricher invocations, zero enrichment counters in the run manifest, and the
end-of-run summary visibly notes the skip. The default path (no flag) must
still run enrichment — guarded here so the skip can't silently leak.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.enrichment import detail as detail_mod


def _scraped_job() -> dict[str, Any]:
    return {
        "url": "https://example.com/job/1",
        "company": "Acme",
        "role": "SRE",
        "source": "remoteok",
        "location": "Remote",
        "comp": "",
        "date_found": "2026-06-10",
    }


def _stub_scrapers(monkeypatch: pytest.MonkeyPatch, jobs: list[dict[str, Any]]) -> None:
    """Make run_all_scrapers return ``jobs`` with no failed sources.

    Drives the append-as-completed callback (``on_source_result``) so the sink
    in run() appends the rows during scraping, mirroring the real orchestrator.
    """
    results = [("remoteok", list(jobs))]

    def fake_run_all(*_a: Any, on_source_result: Any = None, **_kw: Any) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", list(jobs))
        return list(jobs), [], results

    monkeypatch.setattr(runner, "run_all_scrapers", fake_run_all)
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_no_enrich_appends_rows_with_zero_enricher_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run(no_enrich=True) writes the scraped row but invokes no enricher."""
    _stub_scrapers(monkeypatch, [_scraped_job()])

    calls: list[str] = []

    def boom_detail(*_a: Any, **_kw: Any) -> Any:
        calls.append("detail")
        raise AssertionError("detail enrichment must not run with no_enrich")

    def boom_concurrent(*_a: Any, **_kw: Any) -> Any:
        calls.append("concurrent")
        raise AssertionError("fit enrichment must not run with no_enrich")

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", boom_detail)
    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", boom_concurrent)

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        no_enrich=True,
    )

    assert rc == 0
    assert calls == []
    rows = _read_csv(tmp_path / "jobs.csv")
    assert len(rows) == 1
    assert rows[0]["Company"] == "Acme"
    # Unenriched: no LLM-derived fit.
    assert rows[0]["Fit"] == ""


def test_no_enrich_manifest_records_zero_enrichment_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """jobs-last-run.json reports zero enriched_fit_notes."""
    _stub_scrapers(monkeypatch, [_scraped_job()])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("enrichment must not run with no_enrich")

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", boom)
    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", boom)

    runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        no_enrich=True,
    )

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["new_jobs"] == 1
    assert manifest["enriched_fit_notes"] == 0
    assert "enriched_product" not in manifest


def test_no_enrich_summary_notes_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The success line makes the skipped-enrichment state visible."""
    _stub_scrapers(monkeypatch, [_scraped_job()])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_job_details",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("no enrich")),
    )
    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_fit_and_notes",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("no enrich")),
    )

    runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        no_enrich=True,
    )

    err = capsys.readouterr().err
    assert "enrichment skipped" in err.lower()


def test_default_path_still_enriches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the flag, the detail enricher is still invoked (guards the skip),
    and the run path captures descriptions (capture_descriptions defaults True) --
    the mirror of backfill passing False."""
    _stub_scrapers(monkeypatch, [_scraped_job()])

    detail_calls: list[int] = []
    detail_capture: list[bool] = []

    def fake_detail(
        jobs: list[Any],
        ctx: Any,
        *,
        progress: Any = None,
        capture_descriptions: bool = True,
    ) -> Any:
        detail_calls.append(len(jobs))
        detail_capture.append(capture_descriptions)
        return jobs, {
            "enriched": 0,
            "skipped": len(jobs),
            "total": len(jobs),
            "fetched": 0,
        }

    def fake_fit_notes(
        jobs: list[Any],
        ctx: Any,
        *,
        budget: int = 0,
        progress: Any = None,
        flush: Any = None,
        flush_every: int = 25,
        **_kw: Any,
    ) -> Any:
        return (
            jobs,
            {
                "enriched": 0,
                "skipped_budget": 0,
                "failed": 0,
            },
        )

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", fake_detail)
    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)
    # Keep detail's own module attr aligned in case of direct reference.
    monkeypatch.setattr(detail_mod, "enrich_job_details", fake_detail)

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        no_enrich=False,
    )

    assert rc == 0
    assert detail_calls == [1]
    # The run path must NOT suppress description capture (backfill is what passes
    # False); a regression adding capture_descriptions=False to run would fail here.
    assert detail_capture == [True]
