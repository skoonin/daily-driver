from __future__ import annotations

import multiprocessing
import threading
from pathlib import Path

import pytest

from daily_driver.core.locking import file_lock


# Module-level so multiprocessing spawn can pickle it
def _hold_until_signaled(
    lock_path: Path,
    ready_event: multiprocessing.Event,  # type: ignore[valid-type]
    release_event: multiprocessing.Event,  # type: ignore[valid-type]
) -> None:
    """Acquire the lock, signal readiness, then hold until told to release.

    Event-based handoff (not a fixed sleep) so the test never races the spawn:
    the main process waits on ``ready_event`` before attempting its own acquire,
    guaranteeing the lock is genuinely held when the timeout is exercised.
    """
    with file_lock(lock_path):
        ready_event.set()
        release_event.wait(timeout=10)


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
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_hold_until_signaled, args=(lock_path, ready, release)
    )
    proc.start()
    # Block until the subprocess actually holds the lock -- no fixed sleep to
    # race the spawn, so a slow/loaded runner can't acquire before it is held.
    assert ready.wait(timeout=10), "subprocess never acquired the lock"

    try:
        with pytest.raises(TimeoutError, match="Could not acquire lock"):
            with file_lock(lock_path, timeout=0.3):
                pass
    finally:
        release.set()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)


def test_on_contention_not_called_on_uncontended_acquire(tmp_path: Path) -> None:
    lock_path = tmp_path / "free.lock"
    calls: list[int] = []

    with file_lock(lock_path, on_contention=lambda: calls.append(1)):
        pass

    assert calls == []


def test_on_contention_fires_once_then_times_out(tmp_path: Path) -> None:
    lock_path = tmp_path / "contended.lock"
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_hold_until_signaled, args=(lock_path, ready, release)
    )
    proc.start()
    assert ready.wait(timeout=10), "subprocess never acquired the lock"

    calls: list[int] = []
    try:
        with pytest.raises(TimeoutError, match="Could not acquire lock"):
            with file_lock(
                lock_path, timeout=0.3, on_contention=lambda: calls.append(1)
            ):
                pass
    finally:
        release.set()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)

    # The callback fires exactly once per acquire, before the wait — never per
    # poll iteration.
    assert calls == [1]


def test_on_contention_fires_once_then_blocks_until_released(tmp_path: Path) -> None:
    """timeout=None + on_contention: notice once, then block until acquired."""
    lock_path = tmp_path / "blocking.lock"
    calls: list[int] = []
    notice_seen = threading.Event()
    acquired = threading.Event()
    release = threading.Event()

    def _holder() -> None:
        with file_lock(lock_path):
            holder_ready.set()
            release.wait(timeout=10)

    holder_ready = threading.Event()
    holder = threading.Thread(target=_holder)
    holder.start()
    assert holder_ready.wait(timeout=5), "holder never acquired the lock"

    def _notice() -> None:
        calls.append(1)
        notice_seen.set()

    def _waiter() -> None:
        with file_lock(lock_path, on_contention=_notice):
            acquired.set()

    waiter = threading.Thread(target=_waiter)
    waiter.start()
    try:
        # The notice fires before the blocking wait, so it is observable while the
        # lock is still held.
        assert notice_seen.wait(timeout=5), "on_contention never fired"
        assert not acquired.is_set(), "acquired while the lock was still held"
        release.set()
        assert acquired.wait(timeout=5), "never acquired after release"
    finally:
        release.set()
        holder.join(timeout=5)
        waiter.join(timeout=5)

    assert calls == [1]


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
