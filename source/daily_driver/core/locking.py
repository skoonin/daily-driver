"""Advisory file-lock wrapper around fcntl.flock (POSIX only).

Uses LOCK_NB + a polling loop when timeout is set so callers get a clean
TimeoutError instead of an indefinite block. The fd is held open for the
entire lock duration — closing before LOCK_UN would release on some kernels
(Linux auto-releases on last close; the explicit LOCK_UN is belt-and-suspenders).
"""

from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(
    path: Path, *, shared: bool = False, timeout: float | None = None
) -> Iterator[None]:
    """Acquire an advisory flock on path for the duration of the block.

    Raises TimeoutError if the lock cannot be acquired within timeout seconds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    fd = open(path, "ab")
    try:
        if timeout is None:
            fcntl.flock(fd, operation)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Could not acquire lock on {path} within {timeout}s"
                        )
                    time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()
