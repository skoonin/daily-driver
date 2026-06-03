"""Tests for the phased live-progress helper."""

from __future__ import annotations

import io

import pytest
from rich.console import Console as RichConsole

from daily_driver.core.progress import Phase, RunProgress

ANSI = "\x1b["


def _line_console() -> tuple[RichConsole, io.StringIO]:
    """A non-TTY Rich console capturing to a buffer (line-mode target)."""
    buf = io.StringIO()
    console = RichConsole(file=buf, force_terminal=False, color_system=None)
    return console, buf


def test_line_mode_phase_emits_one_line_on_done_no_ansi():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        phase = rp.phase("Detail pages", total=3)
        phase.advance(1)
        phase.advance(1)
        phase.done("2 enriched, 1 skipped (3 total)")
    out = buf.getvalue()
    assert "Detail pages: 2 enriched, 1 skipped (3 total)" in out
    assert ANSI not in out


def test_line_mode_phase_default_summary_reports_count():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        phase = rp.phase("Company products")
        phase.advance(3)
        phase.done()
    assert "Company products: 3 done" in buf.getvalue()


def test_line_mode_advance_is_silent_no_heartbeat():
    """Per-item advance must not emit lines in non-TTY mode."""
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        phase = rp.phase("Fit and notes", total=2)
        phase.advance(1, detail="acme")
        phase.advance(1, detail="globex")
    # Nothing printed until done(); only title (none here) would appear.
    assert buf.getvalue() == ""


def test_line_mode_checklist_prints_header_and_items():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        cl = rp.checklist("Scraping sources")
        cl.start("remoteok").finish(ok=True, detail="14 jobs (3.2s)")
        cl.start("apple").finish(ok=False, detail="failed (timed out)")
    out = buf.getvalue()
    assert "Scraping sources..." in out
    assert "remoteok: 14 jobs (3.2s)" in out
    assert "apple: failed (timed out)" in out
    assert ANSI not in out


def test_title_printed_on_enter():
    console, buf = _line_console()
    with RunProgress(console, tty=False, title="Job search run"):
        pass
    assert "Job search run" in buf.getvalue()


def test_tty_mode_starts_and_stops_progress():
    console = RichConsole(file=io.StringIO(), force_terminal=True, color_system=None)
    with RunProgress(console, tty=True) as rp:
        assert rp._progress is not None
        phase = rp.phase("Detail pages", total=4)
        phase.advance(2, detail="acme")
        assert phase.completed == 2
    assert rp._progress is None  # stopped on exit


def test_tty_mode_stops_progress_on_exception():
    console = RichConsole(file=io.StringIO(), force_terminal=True, color_system=None)
    rp = RunProgress(console, tty=True)
    with pytest.raises(RuntimeError):
        with rp:
            assert rp._progress is not None
            raise RuntimeError("boom")
    assert rp._progress is None  # teardown ran despite the exception


def test_open_counter_phase_advances_without_total():
    console = RichConsole(file=io.StringIO(), force_terminal=True, color_system=None)
    with RunProgress(console, tty=True) as rp:
        phase = rp.phase("Scraping")  # total unknown; open counter
        for _ in range(6):
            phase.advance(1)
        assert phase.completed == 6


def test_advance_matches_progress_callback_signature():
    """Phase.advance must be usable as a ProgressCallback (n, detail)."""
    phase = Phase(None, _line_console()[0], "x", total=2)
    cb = phase.advance
    cb(1, "acme")
    cb(1, None)
    assert phase.completed == 2
