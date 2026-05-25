"""Tests for daily_driver.plugins.job_search.scraper_status."""

from __future__ import annotations

import json
from pathlib import Path

from daily_driver.plugins.job_search import scraper_status


def _write_csv(path: Path, statuses: list[str]) -> None:
    lines = ["Company,status"]
    lines.extend(f"Acme,{s}" for s in statuses)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_last_run_missing_returns_none(tmp_path: Path) -> None:
    assert scraper_status.load_last_run(tmp_path) is None


def test_load_last_run_corrupt_returns_none(tmp_path: Path) -> None:
    (tmp_path / "jobs-last-run.json").write_text("{not json", encoding="utf-8")
    assert scraper_status.load_last_run(tmp_path) is None


def test_load_last_run_parses_payload(tmp_path: Path) -> None:
    payload = {"started_at": "2026-05-25", "new_jobs": 3}
    (tmp_path / "jobs-last-run.json").write_text(json.dumps(payload), encoding="utf-8")
    assert scraper_status.load_last_run(tmp_path) == payload


def test_count_jobs_by_state_buckets_unknown(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_csv(csv_path, ["applied", "applied", "", "interviewing"])
    counts = scraper_status.count_jobs_by_state(csv_path)
    assert counts == {"applied": 2, "unknown": 1, "interviewing": 1}


def test_build_status_counts_awaiting_action(tmp_path: Path) -> None:
    _write_csv(tmp_path / "jobs.csv", ["applied", "interviewing", "rejected"])
    status = scraper_status.build_status(tmp_path)
    assert status["awaiting_action"] == 2
    assert status["last_run"] is None
    assert status["job_counts"]["rejected"] == 1
