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

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext


def _cfg_with_sources(enabled_ids: list[str], *, workers: int = 4) -> ScrapeContext:
    """Build a minimal scraper context that enables the given source IDs."""
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {
                    "enabled": True,
                    "parallel_workers": workers,
                },
                "sources": {sid: {"enabled": True} for sid in enabled_ids},
            }
        )
    )


def test_run_one_logs_starting_at_info(caplog) -> None:
    """`_run_one` emits `[<source>] starting` at INFO before the scraper runs."""
    from daily_driver.plugins.job_search.scraper import runner

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
    from daily_driver.plugins.job_search.scraper import runner

    caplog.set_level(logging.INFO, logger="daily_driver")

    fake = lambda _cfg: []  # noqa: E731
    monkeypatch.setattr(
        runner,
        "SCRAPERS",
        {"remoteok": fake, "greenhouse": fake, "weworkremotely": fake},
    )

    runner.run_all_scrapers(
        _cfg_with_sources(["remoteok", "greenhouse", "weworkremotely"], workers=2)
    )

    msgs = [r.getMessage() for r in caplog.records]
    phase1 = [m for m in msgs if m.startswith("[phase1]")]
    assert phase1, f"expected a [phase1] summary line, got {msgs}"
    # Source names appear in the summary (order-insensitive check).
    summary = phase1[0]
    for sid in ("remoteok", "greenhouse", "weworkremotely"):
        assert sid in summary, f"expected {sid} in phase1 summary, got: {summary}"


def test_run_all_scrapers_phase2_summary_lists_source_names(
    caplog, monkeypatch
) -> None:
    """Phase 2 (non-headless / serial) summary names the sources too."""
    from daily_driver.plugins.job_search.scraper import runner

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
    from daily_driver.plugins.job_search.scraper.runner import _PLAYWRIGHT_SOURCES

    assert "apple" in _PLAYWRIGHT_SOURCES


def test_phase2_runs_visible_browser_by_default(monkeypatch) -> None:
    """Without a pinned block, the serial phase keeps its visible (non-headless)
    browser -- the default that dodges Apple's bot detection."""
    from daily_driver.plugins.job_search.scraper import runner

    seen: list[bool] = []
    monkeypatch.setattr(
        runner,
        "SCRAPERS",
        {"apple": lambda ctx: seen.append(ctx.plugin.scraper.headless) or []},
    )

    runner.run_all_scrapers(_cfg_with_sources(["apple"], workers=1))

    assert seen == [False]


def test_phase2_forces_headless_when_block_active(monkeypatch) -> None:
    """A pinned live block can't share the terminal with a visible Firefox window,
    so force_headless=True runs the serial phase headless."""
    from daily_driver.plugins.job_search.scraper import runner

    seen: list[bool] = []
    monkeypatch.setattr(
        runner,
        "SCRAPERS",
        {"apple": lambda ctx: seen.append(ctx.plugin.scraper.headless) or []},
    )

    runner.run_all_scrapers(
        _cfg_with_sources(["apple"], workers=1), force_headless=True
    )

    assert seen == [True]


def test_run_all_scrapers_adopts_jobspy_loggers(monkeypatch) -> None:
    """A JobSpy site in the run reroutes the JobSpy:* loggers through our handler
    before the parallel phase, so their lines can't bypass the live block.

    Covers the capitalization gap: jobspy logs "finished scraping" via
    ``create_logger(site.value.capitalize())`` (``JobSpy:Linkedin``), a different
    logger from its import-time module one (``JobSpy:LinkedIn``). Both must end
    up on our handler, and a subsequent jobspy ``create_logger`` must NOT re-add
    its own stderr handler.
    """
    import logging as stdlog

    import jobspy

    from daily_driver.core import logging as ddlog
    from daily_driver.core.console import Console
    from daily_driver.plugins.job_search.scraper import runner

    # Mimic the library's import-time module logger: own stderr handler.
    module_logger = stdlog.getLogger("JobSpy:LinkedIn")
    module_logger.handlers.clear()
    module_logger.addHandler(stdlog.StreamHandler())
    module_logger.propagate = False

    Console._user_console = None
    Console._log_console = None
    saved = ddlog._handler
    ddlog.configure("normal")
    our_handler = ddlog._handler

    monkeypatch.setattr(runner, "SCRAPERS", {"linkedin": lambda _ctx: []})
    try:
        runner.run_all_scrapers(
            _cfg_with_sources(["linkedin"], workers=1),
            sources_override=["linkedin"],
        )
        # Both the import-time and the runtime-cased loggers route through us.
        assert module_logger.handlers == [our_handler]
        runtime_logger = stdlog.getLogger("JobSpy:Linkedin")
        assert runtime_logger.handlers == [our_handler]

        # The exact runtime call jobspy makes must not re-add a stderr handler.
        assert jobspy.create_logger("Linkedin").handlers == [our_handler]
    finally:
        ddlog._handler = saved
        module_logger.handlers.clear()
        stdlog.getLogger("JobSpy:Linkedin").handlers.clear()


def test_source_breakdown_segments_maps_funnel_to_colors() -> None:
    """The funnel counts map to coloured segments; the remainder (within-run
    duplicates / url-less rows) becomes the grey 'other' segment."""
    from daily_driver.plugins.job_search.scraper.runner import (
        _source_breakdown_segments,
    )

    # found == new + known + loc_skip -> no remainder.
    segs = _source_breakdown_segments(
        {"found": 61, "new": 40, "known": 15, "loc_skip": 6}
    )
    assert segs == [(40, "green"), (15, "magenta"), (6, "yellow"), (0, "bright_black")]

    # found exceeds the classified survivors -> the gap is the grey remainder.
    segs2 = _source_breakdown_segments(
        {"found": 10, "new": 2, "known": 1, "loc_skip": 1}
    )
    assert segs2[-1] == (6, "bright_black")


def test_run_all_scrapers_notes_jobspy_query_count_once(monkeypatch) -> None:
    """The per-board search count is announced once via on_note -- not per
    JobSpy site -- so two enabled sites don't duplicate the line."""
    from daily_driver.plugins.job_search.scraper import runner

    notes: list[str] = []
    monkeypatch.setattr(
        runner, "SCRAPERS", {"linkedin": lambda _ctx: [], "indeed": lambda _ctx: []}
    )
    runner.run_all_scrapers(
        _cfg_with_sources(["linkedin", "indeed"], workers=1),
        sources_override=["linkedin", "indeed"],
        on_note=notes.append,
    )
    assert len(notes) == 1  # one line for both boards, not one per site
    assert "searches per JobSpy board" in notes[0]


def test_run_all_scrapers_keyboard_interrupt_cancels_and_reraises(
    monkeypatch,
) -> None:
    """Ctrl-C in phase 1: pending futures cancelled, KeyboardInterrupt re-raised.

    The behavioral contract is the ``shutdown`` call shape — patching
    ``ThreadPoolExecutor.shutdown`` lets us assert ``wait=False,
    cancel_futures=True`` deterministically instead of inferring it from a
    wall-clock window, which is flaky under CI load. A loose 10s backstop
    still guards against a regression that blocks on ``wait=True``.

    We intentionally use a worker that loops on a stop flag so it cannot
    outlive the test even if the orchestrator forgets to shut the pool down.
    """
    from concurrent.futures import ThreadPoolExecutor

    from daily_driver.plugins.job_search.scraper import runner

    stop = threading.Event()
    started = threading.Event()
    shutdown_calls: list[dict] = []

    real_shutdown = ThreadPoolExecutor.shutdown

    def recording_shutdown(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        shutdown_calls.append(kwargs)
        # Always tear the pool down non-blocking here so a recorded
        # ``wait=True`` call (the regression) cannot hang the test.
        return real_shutdown(self, wait=False, cancel_futures=True)

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
    monkeypatch.setattr(ThreadPoolExecutor, "shutdown", recording_shutdown)

    cfg = _cfg_with_sources(["slow_src", "quick_src"], workers=2)

    t0 = time.perf_counter()
    try:
        with pytest.raises(KeyboardInterrupt):
            runner.run_all_scrapers(cfg)
        elapsed = time.perf_counter() - t0
        assert shutdown_calls, "expected pool.shutdown() to be called on Ctrl-C"
        assert shutdown_calls[0] == {"wait": False, "cancel_futures": True}, (
            "Ctrl-C must shut the pool down non-blocking with cancelled futures, "
            f"got {shutdown_calls[0]}"
        )
        # Loose backstop: a regression to wait=True would block ~10s.
        assert elapsed < 10.0, f"run_all_scrapers blocked {elapsed:.1f}s on Ctrl-C"
    finally:
        # Release any worker still cooperatively waiting so it doesn't bleed
        # into other tests.
        stop.set()


def test_run_one_reports_completion_via_callback(capsys, monkeypatch) -> None:
    """`_run_one` reports success through on_source_done(sid, ok, detail) and
    no longer prints per-source progress to stdout."""
    from daily_driver.plugins.job_search.scraper import runner

    monkeypatch.setattr(runner, "SCRAPERS", {"fake_src": lambda _cfg: [{"x": 1}]})
    calls: list[tuple[str, bool, str]] = []
    runner._run_one(
        "fake_src",
        _cfg_with_sources(["fake_src"]),
        lambda sid, ok, detail: calls.append((sid, ok, detail)),
    )

    assert len(calls) == 1
    sid, ok, detail = calls[0]
    assert sid == "fake_src"
    assert ok is True
    assert "1 found" in detail
    # No per-source progress leaks to stdout anymore.
    captured = capsys.readouterr()
    assert "Now checking" not in captured.out
    assert "fake_src" not in captured.out


def test_run_all_scrapers_invokes_callback_per_source(monkeypatch) -> None:
    """run_all_scrapers forwards on_source_done to each source it runs."""
    from daily_driver.plugins.job_search.scraper import runner

    def _boom(_cfg):
        raise runner.HTTPTimeout("slow")

    monkeypatch.setattr(
        runner,
        "SCRAPERS",
        {"ok_src": lambda _cfg: [{"x": 1}], "bad_src": _boom},
    )
    calls: list[tuple[str, bool, str]] = []
    runner.run_all_scrapers(
        _cfg_with_sources(["ok_src", "bad_src"]),
        sources_override=["ok_src", "bad_src"],
        on_source_done=lambda sid, ok, detail: calls.append((sid, ok, detail)),
    )

    by_sid = {sid: (ok, detail) for sid, ok, detail in calls}
    assert by_sid["ok_src"][0] is True
    assert by_sid["bad_src"][0] is False
    assert "failed" in by_sid["bad_src"][1]


def test_run_dry_run_non_tty_plain_output(tmp_path, monkeypatch, capsys) -> None:
    """Non-TTY dry-run: progress is plain (no ANSI) on stderr with an ASCII
    funnel, and the dry-run table lands on stdout."""
    from daily_driver.core.console import Console
    from daily_driver.plugins.job_search.scraper import runner

    Console._user_console = None
    Console._log_console = None
    Console.quiet_mode = False
    monkeypatch.setattr(Console, "is_tty", classmethod(lambda cls: False))

    def fake_scrape(
        _ctx,
        *,
        sources_override=None,
        on_source_done=None,
        on_source_start=None,
        on_source_progress=None,
        on_sources_enabled=None,
        on_note=None,
        force_headless=False,
    ):
        if on_sources_enabled is not None:
            on_sources_enabled(["remoteok"])
        if on_source_start is not None:
            on_source_start("remoteok")
        if on_source_done is not None:
            on_source_done("remoteok", True, "1 found in 0.1s")
        job = {
            "company": "Acme",
            "role": "SRE",
            "url": "https://acme.test/1",
            "source": "remoteok",
            "location": "Remote",
        }
        return ([job], [], [("remoteok", [job])])

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    monkeypatch.setattr(runner, "location_matches", lambda _j, _p: True)

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        dry_run=True,
    )

    captured = capsys.readouterr()
    assert rc == 0
    # Completed accounting line, ASCII only (no Unicode arrow).
    assert "Completed:" in captured.err
    assert "found" in captured.err
    assert "→" not in captured.err
    # Non-TTY mode emits no ANSI escape sequences.
    assert "\x1b[" not in captured.err
    # Scraping group summary and per-source row appear on stderr.
    assert "Scraping sources" in captured.err
    assert "remoteok" in captured.err
    # The dry-run table renders on stdout.
    assert "Acme" in captured.out


def test_run_failed_source_returns_exit_code_1(tmp_path, monkeypatch, capsys) -> None:
    """A failed source yields exit code 1 even though the live block tears down
    cleanly around it -- the teardown rewrite must not swallow the failure."""
    from daily_driver.core.console import Console
    from daily_driver.plugins.job_search.scraper import runner

    Console._user_console = None
    Console._log_console = None
    Console.quiet_mode = False
    monkeypatch.setattr(Console, "is_tty", classmethod(lambda cls: False))

    def fake_scrape(
        _ctx,
        *,
        sources_override=None,
        on_source_done=None,
        on_source_start=None,
        on_source_progress=None,
        on_sources_enabled=None,
        on_note=None,
        force_headless=False,
    ):
        if on_sources_enabled is not None:
            on_sources_enabled(["remoteok"])
        if on_source_start is not None:
            on_source_start("remoteok")
        if on_source_done is not None:
            on_source_done("remoteok", False, "failed (timed out)")
        # No jobs, one failed source.
        return ([], ["remoteok"], [("remoteok", runner.HTTPTimeout("slow"))])

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    monkeypatch.setattr(runner, "location_matches", lambda _j, _p: True)

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        dry_run=True,
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert "remoteok" in captured.err


def test_run_keyboard_interrupt_propagates_and_stops_live(
    tmp_path, monkeypatch
) -> None:
    """A ^C during enrichment must unwind the RunProgress context (tearing the
    live display down) and propagate KeyboardInterrupt to the CLI boundary, not
    be swallowed."""
    import logging

    from daily_driver.core import progress as progress_mod
    from daily_driver.core.console import Console
    from daily_driver.plugins.job_search.scraper import runner

    Console._user_console = None
    Console._log_console = None
    Console.quiet_mode = False
    # The live block renders on any TTY now; verbosity only sets stream density.
    monkeypatch.setattr(logging.getLogger("daily_driver"), "level", logging.WARNING)
    monkeypatch.setattr(Console, "is_tty", classmethod(lambda cls: True))

    stops: list[bool] = []
    real_exit = progress_mod.RunProgress.__exit__

    def tracking_exit(self, *exc):
        result = real_exit(self, *exc)
        # __exit__ ran during the unwind and marked the run closed, so any late
        # worker callback is now a no-op.
        stops.append(self._closed)
        return result

    monkeypatch.setattr(progress_mod.RunProgress, "__exit__", tracking_exit)

    monkeypatch.setattr(
        runner,
        "run_all_scrapers",
        lambda _ctx, **_kw: (
            [{"company": "A", "role": "R", "url": "https://a.test/1", "source": "s"}],
            [],
            [],
        ),
    )
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    monkeypatch.setattr(runner, "location_matches", lambda _j, _p: True)

    def interrupt(*_a, **_kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.scraper.enrichment.enrich_job_details_typed",
        interrupt,
    )

    with pytest.raises(KeyboardInterrupt):
        runner.run(
            JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
            tmp_path,
            tmp_path,
            dry_run=False,
        )

    # __exit__ ran during the unwind and closed the run.
    assert stops == [True]
