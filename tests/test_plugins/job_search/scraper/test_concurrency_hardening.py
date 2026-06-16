"""Concurrency-correctness hardening (Wave 2.5 stage 6).

- The SIGINT notifier install/restore is main-thread-only: off-main-thread
  callers get no notifier instead of a ValueError crash.
- The role-matcher cache get/build/set is atomic under a lock.
"""

from __future__ import annotations

import signal
import threading

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment._shared import (
    _install_interrupt_notifier,
    _restore_interrupt_handler,
)
from daily_driver.plugins.job_search.scraper.roles import _matcher_for


def test_install_notifier_off_main_thread_is_noop() -> None:
    """Installing from a worker thread must not raise; returns None."""
    result: list[object] = []
    error: list[BaseException] = []

    def worker() -> None:
        try:
            prev = _install_interrupt_notifier({}, 5, "items")
            result.append(prev)
            # Restore must also be a safe no-op off the main thread.
            _restore_interrupt_handler(prev)
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert not error, f"off-main-thread install raised: {error}"
    assert result == [None]


def test_install_notifier_on_main_thread_installs_and_restores() -> None:
    """On the main thread the notifier installs and restores cleanly."""
    before = signal.getsignal(signal.SIGINT)
    prev = _install_interrupt_notifier({}, 5, "items")
    assert prev is before
    assert signal.getsignal(signal.SIGINT) is not before  # our handler is live
    _restore_interrupt_handler(prev)
    assert signal.getsignal(signal.SIGINT) is before


def test_matcher_cache_returns_stable_instance_across_threads() -> None:
    """_matcher_for must return the one cached matcher per plugin, even when
    first built from concurrent threads (lock makes get/build/set atomic)."""
    plugin = JobSearchPlugin.model_validate({"roles": ["SRE", "Platform Engineer"]})
    results: list[object] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        results.append(_matcher_for(plugin))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    first = results[0]
    assert all(r is first for r in results), "matcher cache returned distinct instances"
