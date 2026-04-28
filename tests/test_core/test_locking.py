from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from daily_driver.core.locking import file_lock


# Module-level so multiprocessing spawn can pickle it
def _hold_lock(p: Path, hold_seconds: float) -> None:
    with file_lock(p):
        time.sleep(hold_seconds)


def test_basic_lock_acquire_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    with file_lock(lock_path):
        assert lock_path.exists()


def test_creates_parent_dirs(tmp_path: Path) -> None:
    lock_path = tmp_path / "nested" / "deep" / "test.lock"
    with file_lock(lock_path):
        assert lock_path.parent.is_dir()


def test_exception_inside_block_releases_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    with pytest.raises(RuntimeError):
        with file_lock(lock_path):
            raise RuntimeError("inner failure")

    # Lock must be released — re-acquiring with timeout should succeed
    with file_lock(lock_path, timeout=1.0):
        pass


def test_timeout_raises(tmp_path: Path) -> None:
    lock_path = tmp_path / "timeout.lock"
    proc = multiprocessing.Process(target=_hold_lock, args=(lock_path, 2.0))
    proc.start()
    # Give the subprocess time to acquire the lock before we try
    time.sleep(0.2)

    try:
        with pytest.raises(TimeoutError, match="Could not acquire lock"):
            with file_lock(lock_path, timeout=0.3):
                pass
    finally:
        proc.terminate()
        proc.join(timeout=3)


def test_sequential_locks_work(tmp_path: Path) -> None:
    lock_path = tmp_path / "seq.lock"
    with file_lock(lock_path):
        pass
    with file_lock(lock_path):
        pass


def _acquire_and_signal(lock_path: Path, ready_event: multiprocessing.Event, done_event: multiprocessing.Event) -> None:  # type: ignore[type-arg]
    with file_lock(lock_path):
        ready_event.set()
        done_event.wait(timeout=5)


def test_concurrent_exclusive_blocks(tmp_path: Path) -> None:
    lock_path = tmp_path / "concurrent.lock"
    ready = multiprocessing.Event()
    done = multiprocessing.Event()

    proc = multiprocessing.Process(
        target=_acquire_and_signal, args=(lock_path, ready, done)
    )
    proc.start()
    ready.wait(timeout=3)

    try:
        with pytest.raises(TimeoutError):
            with file_lock(lock_path, timeout=0.2):
                pass
    finally:
        done.set()
        proc.join(timeout=3)
