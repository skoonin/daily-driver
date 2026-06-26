"""Advisory file-lock wrapper around fcntl.flock (POSIX only).

Acquires a blocking flock by default. When a ``timeout`` or an ``on_contention``
callback is given, it tries a non-blocking acquire first and, if the lock is
already held, invokes ``on_contention`` once before waiting — then blocks until
the holder releases (``timeout`` None) or polls until the deadline, raising a
clean TimeoutError instead of an indefinite block. The fd is held open for the
entire lock duration — closing before LOCK_UN would release on some kernels
(Linux auto-releases on last close; the explicit LOCK_UN is belt-and-suspenders).
"""

from __future__ import annotations

import fcntl
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO


@contextmanager
def file_lock(
    path: Path,
    *,
    shared: bool = False,
    timeout: float | None = None,
    on_contention: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Acquire an advisory flock on path for the duration of the block.

    Raises TimeoutError if the lock cannot be acquired within timeout seconds.

    ``on_contention`` is invoked at most once per acquire, the moment the lock is
    found to be held by someone else, before the (possibly long) wait begins —
    so callers can announce a wait instead of parking silently. It never fires on
    an uncontended acquire. When ``on_contention`` is None and ``timeout`` is
    None the original single blocking flock is taken, byte-for-byte unchanged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    fd = open(path, "ab")
    try:
        if on_contention is None and timeout is None:
            fcntl.flock(fd, operation)
        else:
            try:
                fcntl.flock(fd, operation | fcntl.LOCK_NB)
            except BlockingIOError:
                if on_contention is not None:
                    on_contention()
                _wait_for_lock(fd, operation, path, timeout)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()


def _wait_for_lock(
    fd: IO[bytes], operation: int, path: Path, timeout: float | None
) -> None:
    """Block (timeout=None) or poll until the deadline for an already-contended fd."""
    if timeout is None:
        fcntl.flock(fd, operation)
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, operation | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not acquire lock on {path} within {timeout}s"
                )
            time.sleep(0.05)
