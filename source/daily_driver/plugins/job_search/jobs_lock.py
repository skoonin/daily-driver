from __future__ import annotations

from pathlib import Path


def jobs_lock_path(jobs_csv: Path) -> Path:
    """Return the sentinel lockfile path used to serialize jobs mutations."""
    return jobs_csv.with_name(".jobs.lock")
