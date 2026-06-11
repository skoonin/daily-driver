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

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner


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
