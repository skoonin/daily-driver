"""Verbose-mode source visibility for ``daily-driver jobs run -v``.

Asserts that the orchestrator emits per-source ``starting`` lines and that
phase summary lines list source names (not just counts) at INFO level so
``-v`` makes it obvious which scraper is running at any given moment.

KeyboardInterrupt handling around the phase-1 ThreadPoolExecutor is also
covered here: a ^C during a parallel run must cancel pending futures and
re-raise without hanging on ``with``-block exit.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest


def _cfg_with_sources(enabled_ids: list[str], *, workers: int = 4) -> dict:
    """Build a minimal scraper config that enables the given source IDs."""
    return {
        "job_search": {
            "scraper": {
                "enabled": True,
                "parallel_workers": workers,
                "sources": {sid: {"enabled": True} for sid in enabled_ids},
            }
        }
    }


def test_run_one_logs_starting_at_info(caplog) -> None:
    """`_run_one` emits `[<source>] starting` at INFO before the scraper runs."""
    from daily_driver.scraper import runner

    caplog.set_level(logging.INFO, logger="daily_driver")

    fake = lambda _cfg: []  # noqa: E731 — terse stub for test
    monkeyed = {"remoteok": fake}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner, "SCRAPERS", monkeyed)
        runner._run_one("remoteok", _cfg_with_sources(["remoteok"]))

    starting = [r for r in caplog.records if "[remoteok] starting" in r.getMessage()]
    assert starting, "expected a `[remoteok] starting` INFO line"
    assert all(r.levelno == logging.INFO for r in starting)


def test_run_all_scrapers_phase1_summary_lists_source_names(
    caplog, monkeypatch
) -> None:
    """Phase 1 summary names the sources, not just the count."""
    from daily_driver.scraper import runner

    caplog.set_level(logging.INFO, logger="daily_driver")

    fake = lambda _cfg: []  # noqa: E731
    monkeypatch.setattr(
        runner, "SCRAPERS", {"remoteok": fake, "greenhouse": fake, "jobspy": fake}
    )

    runner.run_all_scrapers(
        _cfg_with_sources(["remoteok", "greenhouse", "jobspy"], workers=2)
    )

    msgs = [r.getMessage() for r in caplog.records]
    phase1 = [m for m in msgs if m.startswith("[phase1]")]
    assert phase1, f"expected a [phase1] summary line, got {msgs}"
    # Source names appear in the summary (order-insensitive check).
    summary = phase1[0]
    for sid in ("remoteok", "greenhouse", "jobspy"):
        assert sid in summary, f"expected {sid} in phase1 summary, got: {summary}"


def test_run_all_scrapers_phase2_summary_lists_source_names(
    caplog, monkeypatch
) -> None:
    """Phase 2 (non-headless / serial) summary names the sources too."""
    from daily_driver.scraper import runner

    caplog.set_level(logging.INFO, logger="daily_driver")

    fake = lambda _cfg: []  # noqa: E731
    monkeypatch.setattr(runner, "SCRAPERS", {"apple": fake})

    runner.run_all_scrapers(_cfg_with_sources(["apple"], workers=1))

    msgs = [r.getMessage() for r in caplog.records]
    phase2 = [m for m in msgs if m.startswith("[phase2]")]
    assert phase2, f"expected a [phase2] summary line, got {msgs}"
    assert "apple" in phase2[0], f"expected `apple` in phase2 summary, got: {phase2[0]}"


def test_apple_is_classified_as_playwright_source() -> None:
    """apple is always classified as non-headless via the code-level registry."""
    from daily_driver.scraper.runner import _PLAYWRIGHT_SOURCES

    assert "apple" in _PLAYWRIGHT_SOURCES


def test_run_all_scrapers_keyboard_interrupt_cancels_and_reraises(
    monkeypatch,
) -> None:
    """Ctrl-C in phase 1: pending futures cancelled, KeyboardInterrupt re-raised,
    and the call returns within seconds (not waiting for slow workers).

    We intentionally use a worker that loops on a stop flag so it cannot
    outlive the test even if the orchestrator forgets to shut the pool down;
    the assertion is on wall-clock elapsed time before re-raise.
    """
    from daily_driver.scraper import runner

    stop = threading.Event()
    started = threading.Event()

    def slow(_cfg: dict) -> list[dict]:
        started.set()
        # Cooperative wait: if the test is going to fail because the executor
        # is still holding us, we still cap at ~10s wall-clock.
        stop.wait(timeout=10)
        return []

    def kb_as_completed(_futures, **_kw):  # type: ignore[no-untyped-def]
        # Wait until a worker has actually started, then raise to mimic SIGINT
        # delivery during the orchestrator's wait on ``as_completed``.
        started.wait(timeout=5)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "SCRAPERS", {"slow_src": slow, "quick_src": slow})
    monkeypatch.setattr(runner, "as_completed", kb_as_completed)

    cfg = _cfg_with_sources(["slow_src", "quick_src"], workers=2)

    t0 = time.perf_counter()
    try:
        with pytest.raises(KeyboardInterrupt):
            runner.run_all_scrapers(cfg)
        elapsed = time.perf_counter() - t0
        # If pool.shutdown(wait=True) is used (the default for `with`), this
        # blocks ~10s. With wait=False + cancel_futures=True it returns at
        # once. Headroom of a few seconds for thread bookkeeping.
        assert elapsed < 4.0, f"run_all_scrapers blocked {elapsed:.1f}s on Ctrl-C"
    finally:
        # Release any worker still cooperatively waiting so it doesn't bleed
        # into other tests.
        stop.set()
