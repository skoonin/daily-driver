"""L-2: jobs lock sentinel lives under the ephemeral state dir, not beside jobs.csv."""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_driver.plugins.job_search.jobs_lock import (
    clear_stale_adjacent_lock,
    jobs_lock_path,
)


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


def test_clear_stale_adjacent_lock_removes_zombie(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text("Status,Link\n", encoding="utf-8")
    stale = csv_path.with_name(".jobs.lock")
    stale.write_text("", encoding="utf-8")
    assert stale.exists()

    clear_stale_adjacent_lock(csv_path)

    assert not stale.exists()
    # The data file itself must never be touched.
    assert csv_path.exists()


def test_clear_stale_adjacent_lock_noop_when_absent(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text("Status,Link\n", encoding="utf-8")
    # Must not raise when there is no stale lock to remove.
    clear_stale_adjacent_lock(csv_path)
    assert csv_path.exists()


def test_clear_stale_adjacent_lock_swallows_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PermissionError on the cosmetic legacy-lock cleanup must not abort the
    caller's scrape/backfill/prune."""
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text("Status,Link\n", encoding="utf-8")

    def boom(*_a: object, **_kw: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", boom)

    # Does not raise.
    clear_stale_adjacent_lock(csv_path)
