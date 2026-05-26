"""Tests for daily_driver.plugins.job_search.doctor — jobs.csv backup pile-up."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from daily_driver.plugins.job_search import doctor


@dataclass
class _FakeWorkspace:
    output_dir: Path


def _make_baks(backups_dir: Path, count: int) -> None:
    backups_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (backups_dir / f"jobs.csv.bak.2026-05-{i:02d}").write_text(
            "x", encoding="utf-8"
        )


def test_no_backups_dir_returns_empty(tmp_path: Path) -> None:
    ws = _FakeWorkspace(output_dir=tmp_path)
    assert doctor.run_checks(ws) == []


def test_few_backups_below_threshold_no_row(tmp_path: Path) -> None:
    _make_baks(tmp_path / "backups", 5)
    ws = _FakeWorkspace(output_dir=tmp_path)
    assert doctor.run_checks(ws) == []


def test_accumulated_backups_warns(tmp_path: Path) -> None:
    _make_baks(tmp_path / "backups", 8)
    ws = _FakeWorkspace(output_dir=tmp_path)
    results = doctor.run_checks(ws)
    assert len(results) == 1
    row = results[0]
    assert row.name == "Jobs backups"
    assert row.status == "WARNING"
    assert not row.fixable
