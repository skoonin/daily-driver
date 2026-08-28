from __future__ import annotations

from pathlib import Path

from daily_driver.core.console import Console

# Top-level jobs acquisitions wait this long for a peer command to finish before
# giving up, so a wedged holder can never hang the caller indefinitely.
LOCK_WAIT_TIMEOUT_SECONDS = 600.0

LOCK_GIVEUP_MESSAGE = (
    "jobs workspace lock still held after waiting; giving up -- "
    "retry once the other command finishes."
)


class JobsLockTimeout(RuntimeError):
    """Raised when a jobs workspace lock is not acquired before the deadline."""


def workspace_busy_notice() -> None:
    """Announce (on stderr) that a jobs command is waiting on the workspace lock.

    Wired as ``file_lock(on_contention=...)`` so it fires once, the moment
    contention is detected, turning the previously silent wait into a visible
    one. Intentionally generic: it does not read the holder's identity.
    """
    Console.warning(
        "Another jobs command is writing the workspace; waiting for it to finish..."
    )


def jobs_lock_path(ephemeral_dir: Path) -> Path:
    """Return the sentinel lockfile path used to serialize jobs mutations.

    The sentinel lives under the workspace ephemeral state dir, never beside
    the data file it guards (see developer.md "Flock model").
    """
    return ephemeral_dir / "jobs.lock"
