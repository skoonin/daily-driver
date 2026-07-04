"""Locking and interrupt persistence tests for jobs CSV operations."""

from __future__ import annotations

import csv
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from daily_driver.core.locking import file_lock as core_file_lock
from daily_driver.plugins.job_search import jobs_archive
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.jobs_lock import jobs_lock_path
from daily_driver.plugins.job_search.scraper import csv_io, runner


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
        "Date Verified": "2026-04-01",
        "Date Applied": "",
        "Link": link,
        "Source": "remoteok",
    }


def _stub_detail_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_detail(
        jobs: list[Any],
        ctx: Any,
        *,
        progress: Any = None,
        capture_descriptions: bool = True,
    ) -> Any:
        if progress is not None:
            progress(len(jobs))
        return jobs, {
            "total": len(jobs),
            "fetched": 0,
            "enriched": 0,
            "failed": 0,
            "skipped": len(jobs),
            "skip_reasons": {},
        }

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", fake_detail)


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
    _stub_detail_noop(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def interrupting_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        # Replace slot 0 in place (mirrors the real enricher) before raising so
        # backfill's interrupt handler can persist the partial result.
        jobs[0] = jobs[0].with_updates(fit=8, notes="Saved before interrupt")
        raise KeyboardInterrupt

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_fit_and_notes",
        interrupting_fit_notes,
    )

    with pytest.raises(KeyboardInterrupt):
        runner.run_backfill(JobSearchPlugin(), csv_path, tmp_path)

    rows = _read_jobs_csv(csv_path)
    assert rows[0]["Fit"] == "8"
    assert rows[0]["Notes"] == "Saved before interrupt"

    backups_dir = tmp_path / "backups"
    backups = [p for p in backups_dir.iterdir() if p.name.startswith("jobs.csv.bak.")]
    assert len(backups) == 1
    # Backup must capture pre-mutation state (made before enrichment ran).
    backup_rows = _read_jobs_csv(backups[0])
    assert backup_rows[0]["Fit"] == ""
    assert backup_rows[0]["Notes"] == ""

    # Interrupt message should name the backup file so the user can recover.
    captured = capsys.readouterr()
    assert backups[0].name in captured.err


def test_backfill_uses_shared_jobs_lock_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="Acme", link="https://example.com/1")])
    _stub_detail_noop(monkeypatch)

    lock_calls: list[Path] = []

    @contextmanager
    def fake_file_lock(path: Path, **_kwargs: Any):
        lock_calls.append(path)
        yield

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def enrich_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        jobs[0] = jobs[0].with_updates(fit=7, notes="Saved")
        return (
            jobs,
            {"enriched": 1, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(runner, "file_lock", fake_file_lock)
    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", enrich_fit_notes)

    runner.run_backfill(JobSearchPlugin(), csv_path, tmp_path)

    # The sentinel lock is taken exactly once: backfill holds it across the whole
    # read -> enrich -> rewrite window (the sink does NOT re-take it per flush).
    assert lock_calls == [jobs_lock_path(tmp_path)]
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
        lambda _csv_path: (set(), set(), {}),
    )

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        dry_run=True,
    )

    assert rc == 0
    assert lock_calls == [jobs_lock_path(tmp_path)]


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

    jobs_archive.prune(csv_path, tmp_path, cutoff=date(2026, 5, 1), dry_run=False)

    assert lock_calls == [jobs_lock_path(tmp_path)]


def test_run_emits_contention_notice_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second jobs command hitting a held sentinel prints a visible stderr
    notice instead of hanging silently; stdout stays clean."""
    from daily_driver.plugins.job_search.jobs_lock import (
        workspace_busy_notice as real_notice,
    )

    monkeypatch.setattr(runner, "run_all_scrapers", lambda *_a, **_kw: ([], [], []))
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )

    lock_path = jobs_lock_path(tmp_path)
    holder_ready = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        # Real cross-fd flock contention: this holder and run()'s acquire open
        # the sentinel on separate descriptors, so flock genuinely conflicts.
        with core_file_lock(lock_path):
            holder_ready.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=_hold)
    holder.start()
    assert holder_ready.wait(timeout=5), "holder never acquired the lock"

    # Deterministic handoff: the contention notice fires (on run()'s thread)
    # before the blocking wait, so releasing the holder FROM the notice itself
    # guarantees run() genuinely contended -- no timer that could free the lock
    # before run() ever reaches it.
    def _release_on_notice() -> None:
        release.set()
        real_notice()

    monkeypatch.setattr(runner, "workspace_busy_notice", _release_on_notice)
    try:
        rc = runner.run(
            JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
            tmp_path,
            tmp_path,
            dry_run=True,
            suppress_live=True,
        )
    finally:
        release.set()
        holder.join(timeout=5)

    assert rc == 0
    captured = capsys.readouterr()
    # Substring kept short so Rich soft-wrapping the line can't split the match.
    assert "Another jobs command is writing the workspace" in captured.err
    # The notice must never leak onto stdout (keeps `--json` output valid).
    assert "Another jobs command is writing the workspace" not in captured.out
