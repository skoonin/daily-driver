"""L-2: jobs lock sentinel lives under the ephemeral state dir, not beside jobs.csv."""

from __future__ import annotations

from pathlib import Path

from daily_driver.plugins.job_search.jobs_lock import jobs_lock_path


def test_jobs_lock_path_resolves_under_ephemeral_dir(tmp_path: Path) -> None:
    ephemeral_dir = tmp_path / ".daily-driver" / "state"
    lock = jobs_lock_path(ephemeral_dir)
    assert lock == ephemeral_dir / "jobs.lock"


def test_jobs_lock_path_not_beside_jobs_csv(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    ephemeral_dir = tmp_path / ".daily-driver" / "state"
    csv_path = output_dir / "jobs.csv"
    lock = jobs_lock_path(ephemeral_dir)
    assert lock.parent != csv_path.parent
    assert lock != csv_path.with_name(".jobs.lock")
