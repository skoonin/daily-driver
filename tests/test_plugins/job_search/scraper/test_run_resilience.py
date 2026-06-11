"""Run-resilience: per-source append, enrichment flush, overlap, SIGTERM/manifest.

The durable record (jobs.csv) is the checkpoint: each source's rows are appended
as it completes, enrichment updates rows in place with periodic flushes, and a
crash/interrupt loses at most one source or one flush window. These tests inject
failures via stubs (no real signals where avoidable) so the resilience claims are
deterministic.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from daily_driver.core.config_models import AIConfig
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched


def _scraped(url: str, company: str, role: str = "SRE", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "url": url,
        "company": company,
        "role": role,
        "source": extra.pop("source", "remoteok"),
        "location": extra.pop("location", "Remote"),
        "comp": "",
        "date_found": "2026-06-10",
    }
    base.update(extra)
    return base


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _us_remote_plugin() -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "roles": ["Engineer"],
            "locations": {"countries": ["US"], "remote": True},
        }
    )


# ── Stage 1: per-source append ───────────────────────────────────────────────


def test_append_jobs_for_source_writes_and_updates_dedup(tmp_path: Path) -> None:
    """A sink appends one source's deduped/filtered/lifted rows and grows its
    known-url set so a later source dedups against them (cross-source)."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CANONICAL_HEADER)

    sink = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=CANONICAL_HEADER,
        known_urls=set(),
        known_keys=set(),
        plugin=_us_remote_plugin(),
    )

    counts_a = sink.append_source(
        "src_a", [_scraped("https://a/1", "Acme"), _scraped("https://a/2", "Bravo")]
    )
    assert counts_a["new"] == 2
    # The Apple wave (later source) must dedup against what src_a already wrote.
    counts_b = sink.append_source(
        "src_b", [_scraped("https://a/1", "Acme"), _scraped("https://b/3", "Charlie")]
    )
    assert counts_b["new"] == 1
    assert counts_b["known"] == 1

    rows = _read_csv(csv_path)
    assert [r["Company"] for r in rows] == ["Acme", "Bravo", "Charlie"]
    # Master row list holds every appended job for the enrichment pass.
    assert len(sink.rows) == 3


def test_append_source_location_filters_and_drops_urlless(tmp_path: Path) -> None:
    """Per-source append mirrors run()'s funnel: location-skip and url-less drop."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CANONICAL_HEADER)
    sink = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=CANONICAL_HEADER,
        known_urls=set(),
        known_keys=set(),
        plugin=runner_us_only_plugin(),
    )
    counts = sink.append_source(
        "src",
        [
            _scraped("https://a/1", "Acme", location="Seattle, United States"),
            _scraped("https://a/2", "Bravo", location="Berlin, Germany"),
            _scraped("", "NoUrl", location="Seattle, United States"),
        ],
    )
    assert counts["new"] == 1
    assert counts["loc_skip"] == 1
    rows = _read_csv(csv_path)
    assert [r["Company"] for r in rows] == ["Acme"]


def runner_us_only_plugin() -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "roles": ["Engineer"],
            "locations": {"countries": ["US"], "remote": False},
        }
    )


def test_run_appends_per_source_then_crash_keeps_first_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after source A appended must leave A's rows in jobs.csv.

    The scraper orchestrator is stubbed to append source A through the sink
    callback, then raise before source B — proving rows survive a mid-scrape
    crash without a final batch append.
    """
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        assert on_source_result is not None
        on_source_result("src_a", [_scraped("https://a/1", "Acme")])
        raise RuntimeError("scraper crashed mid-run")

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    with pytest.raises(RuntimeError):
        runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)

    rows = _read_csv(tmp_path / "jobs.csv")
    assert [r["Company"] for r in rows] == ["Acme"]


def test_dry_run_appends_nothing_per_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run keeps the in-memory single-pass behavior: no writes at all."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    results = [("remoteok", [_scraped("https://a/1", "Acme")])]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", [_scraped("https://a/1", "Acme")])
        return [_scraped("https://a/1", "Acme")], [], results

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, dry_run=True)
    assert rc == 0
    # No csv written at all under dry-run.
    assert (
        not (tmp_path / "jobs.csv").exists() or _read_csv(tmp_path / "jobs.csv") == []
    )


# ── Stage 2: enrichment in-place flush ───────────────────────────────────────


def _enrich_plugin(budget: int = 50) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_companies": budget,
                "max_enrich_fit": budget,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            },
        }
    )


def _serial_ctx(budget: int = 50) -> ScrapeContext:
    # max_parallel=1 -> serial provider path: deterministic ordering, no threads.
    return ScrapeContext(
        plugin=_enrich_plugin(budget),
        ai=AIConfig.model_validate({"claude": {"max_parallel": 1}}),
    )


def test_concurrent_enrichment_flushes_every_n_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coordinator calls the flush hook every ``flush_every`` applied results.

    With 7 fit jobs and flush_every=3, flush fires after results 3 and 6 inside
    the loop, then once on phase completion by the caller -- but the coordinator
    itself triggers at the 3-result boundaries.
    """
    from daily_driver.integrations import ai_provider
    from daily_driver.plugins.job_search.scraper import enrichment

    monkeypatch.setattr(
        ai_provider,
        "invoke_for",
        lambda prompt, **kw: '{"fit": 6, "notes": "ok"}',
    )
    jobs = [make_enriched(company=f"Co{i}", url=f"https://x/{i}") for i in range(7)]
    flush_calls: list[int] = []
    enrichment.enrich_product_and_fit_concurrently(
        jobs,
        _serial_ctx(),
        flush=lambda: flush_calls.append(1),
        flush_every=3,
    )
    # 7 fit results -> flush at 3 and 6 (the company pass writes products too,
    # but the fit pass alone guarantees at least the two boundary flushes).
    assert len(flush_calls) >= 2


def test_run_flushes_enrichment_progress_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After enrichment, run() rewrites jobs.csv so Fit/Notes land on disk."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    # comp set -> detail enricher skips the page fetch (no real network).
    jobs = [
        _scraped("https://x/1", "Acme", comp="$200k"),
        _scraped("https://x/2", "Bravo", comp="$200k"),
    ]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda prompt, **kw: '{"fit": 8, "notes": "great"}'
    )

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0
    rows = _read_csv(tmp_path / "jobs.csv")
    assert [r["Company"] for r in rows] == ["Acme", "Bravo"]
    assert all(r["Fit"] == "8" for r in rows)


def test_run_interrupt_mid_enrichment_flushes_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A KeyboardInterrupt mid-enrichment leaves the flushed rows on disk.

    A serial provider applies fit results in order, one per call; the stub
    raises on the second call. run() must flush the partial progress before the
    interrupt propagates, so the first row's Fit survives. Product enrichment is
    off so only the fit pass runs (its serial path applies each result as the
    call settles, before the next fetch).
    """
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_fit": 50,
                "enrich_product": False,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            },
        }
    )
    jobs = [_scraped(f"https://x/{i}", f"Co{i}", comp="$200k") for i in range(4)]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    from daily_driver.integrations import ai_provider

    calls = [0]

    def fake_invoke(prompt: str, **kw: Any) -> str:
        calls[0] += 1
        if calls[0] >= 2:
            raise KeyboardInterrupt
        return '{"fit": 7, "notes": "first"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    with pytest.raises(KeyboardInterrupt):
        runner.run(plugin, tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False)

    rows = _read_csv(tmp_path / "jobs.csv")
    # All rows still present (appended during scraping); at least one enriched
    # row survived the flush-on-interrupt.
    assert len(rows) == 4
    assert any(r["Fit"] == "7" for r in rows)
