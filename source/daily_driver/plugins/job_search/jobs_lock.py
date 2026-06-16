from __future__ import annotations

from pathlib import Path

from daily_driver.core.logging import get_logger

log = get_logger(__name__)


def jobs_lock_path(ephemeral_dir: Path) -> Path:
    """Return the sentinel lockfile path used to serialize jobs mutations.

    The sentinel lives under the workspace ephemeral state dir, never beside
    the data file it guards (see developer.md "Flock model").
    """
    return ephemeral_dir / "jobs.lock"


def clear_stale_adjacent_lock(jobs_csv: Path) -> None:
    """Remove a zombie ``.jobs.lock`` left beside jobs.csv by older versions.

    The sentinel used to live in the user-visible output dir; opportunistically
    unlink it so users do not keep a stale file once the new state-dir sentinel
    is in use. Never touches the data file.
    """
    stale = jobs_csv.with_name(".jobs.lock")
    try:
        stale.unlink(missing_ok=True)
    except OSError as exc:
        # Cleanup is cosmetic; a permission error on the legacy lock must never
        # abort the scrape/backfill/prune that owns the real state-dir sentinel.
        log.debug("could not remove legacy lock %s: %s", stale, exc)
