"""Locking and interrupt persistence tests for jobs CSV operations."""

from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search import jobs_archive
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.jobs_lock import jobs_lock_path
from daily_driver.plugins.job_search.scraper import csv_io, runner
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext


def _write_jobs_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=csv_io.CANONICAL_HEADER,
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_jobs_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _row(*, company: str, link: str, status: str = "found") -> dict[str, str]:
    return {
        "Status": status,
        "Notes": "",
        "Company": company,
        "Location": "Remote",
        "Role": "SRE",
        "Fit": "",
        "Comp": "$180,000-$220,000",
        "Date Found": "2026-04-01",
        "Date Last Seen": "2026-04-01",
        "Date Applied": "",
        "Link": link,
        "Product/Purpose": "",
        "GD Rating": "",
        "Source": "remoteok",
    }


def test_backfill_keyboard_interrupt_saves_partial_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [
            _row(company="Acme", link="https://example.com/1"),
            _row(company="Bravo", link="https://example.com/2"),
        ],
    )

    from daily_driver.plugins.job_search.scraper.enrichment import llm as enrichment

    def interrupting_enrich_company(
        jobs: list[dict[str, Any]], ctx: Any = None, *, budget: int = 0
    ) -> dict[str, int]:
        jobs[0]["product"] = "Saved before interrupt"
        jobs[0]["gd_rating"] = "4.2"
        raise KeyboardInterrupt

    def should_not_run(*_a: Any, **_kw: Any) -> dict[str, int]:
        raise AssertionError("fit+notes enricher must not run after interrupt")

    monkeypatch.setattr(
        enrichment,
        "enrich_company_descriptions",
        interrupting_enrich_company,
    )
    monkeypatch.setattr(enrichment, "enrich_fit_and_notes", should_not_run)

    with pytest.raises(KeyboardInterrupt):
        csv_io.backfill(ScrapeContext(plugin=JobSearchPlugin()), csv_path)

    rows = _read_jobs_csv(csv_path)
    assert rows[0]["Product/Purpose"] == "Saved before interrupt"
    assert rows[0]["GD Rating"] == "4.2"

    backups_dir = tmp_path / "backups"
    backups = [p for p in backups_dir.iterdir() if p.name.startswith("jobs.csv.bak.")]
    assert len(backups) == 1
    # Backup must capture pre-mutation state (made before enrichment ran).
    backup_rows = _read_jobs_csv(backups[0])
    assert backup_rows[0]["Product/Purpose"] == ""
    assert backup_rows[0]["GD Rating"] == ""

    # Interrupt message should name the backup file so the user can recover.
    captured = capsys.readouterr()
    assert backups[0].name in captured.err


def test_backfill_uses_shared_jobs_lock_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="Acme", link="https://example.com/1")])

    lock_calls: list[Path] = []

    @contextmanager
    def fake_file_lock(path: Path, **_kwargs: Any):
        lock_calls.append(path)
        yield

    from daily_driver.plugins.job_search.scraper.enrichment import llm as enrichment

    def enrich_company(
        jobs: list[dict[str, Any]], ctx: Any = None, *, budget: int = 0
    ) -> dict[str, int]:
        jobs[0]["product"] = "Acme product"
        jobs[0]["gd_rating"] = "unknown"
        return {"enriched": 1, "skipped_cached": 0, "failed": 0}

    def enrich_fit_notes(
        jobs: list[dict[str, Any]], ctx: Any = None, *, budget: int = 0
    ) -> dict[str, int]:
        jobs[0]["fit"] = "7"
        jobs[0]["notes"] = "Saved"
        return {"enriched": 1, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}

    monkeypatch.setattr(csv_io, "file_lock", fake_file_lock)
    monkeypatch.setattr(enrichment, "enrich_company_descriptions", enrich_company)
    monkeypatch.setattr(enrichment, "enrich_fit_and_notes", enrich_fit_notes)

    csv_io.backfill(ScrapeContext(plugin=JobSearchPlugin()), csv_path)

    assert lock_calls == [jobs_lock_path(csv_path)]
    # Exactly one .bak per backfill run (taken at the start, before mutations).
    backups_dir = tmp_path / "backups"
    backups = [p for p in backups_dir.iterdir() if p.name.startswith("jobs.csv.bak.")]
    assert len(backups) == 1


def test_run_uses_shared_jobs_lock_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_calls: list[Path] = []

    @contextmanager
    def fake_file_lock(path: Path, **_kwargs: Any):
        lock_calls.append(path)
        yield

    monkeypatch.setattr(runner, "file_lock", fake_file_lock)
    monkeypatch.setattr(runner, "run_all_scrapers", lambda *_a, **_kw: ([], [], []))
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        dry_run=True,
    )

    assert rc == 0
    assert lock_calls == [jobs_lock_path(tmp_path / "jobs.csv")]


def test_prune_uses_shared_jobs_lock_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [
            _row(
                company="OldCo",
                link="https://example.com/old",
                status="rejected",
            )
        ],
    )

    lock_calls: list[Path] = []

    @contextmanager
    def fake_file_lock(path: Path, **_kwargs: Any):
        lock_calls.append(path)
        yield

    monkeypatch.setattr(jobs_archive, "file_lock", fake_file_lock)

    jobs_archive.prune(csv_path, cutoff=date(2026, 5, 1), dry_run=False)

    assert lock_calls == [jobs_lock_path(csv_path)]
