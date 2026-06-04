"""Tests for the phased live-progress helper."""

from __future__ import annotations

import io

import pytest
from rich.console import Console as RichConsole

from daily_driver.core.progress import Group, RunProgress

ANSI = "\x1b["


def _line_console() -> tuple[RichConsole, io.StringIO]:
    """A non-TTY Rich console capturing to a buffer (line-mode target)."""
    buf = io.StringIO()
    console = RichConsole(file=buf, force_terminal=False, color_system=None)
    return console, buf


def _tty_console() -> RichConsole:
    return RichConsole(file=io.StringIO(), force_terminal=True, color_system=None)


def test_line_mode_item_prints_on_finish_no_ansi():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        g = rp.group("Scraping sources")
        item = g.item("apple")
        item.start()  # silent in line mode
        item.finish(ok=True, detail="128 jobs (17 dup)")
    out = buf.getvalue()
    assert "apple: 128 jobs (17 dup)" in out
    assert ANSI not in out


def test_line_mode_failed_item_prints_detail():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        g = rp.group("Scraping sources")
        g.item("linkedin").finish(ok=False, detail="failed (timed out)")
    assert "linkedin: failed (timed out)" in buf.getvalue()


def test_line_mode_phase_prints_summary_on_done():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        g = rp.group("Enriching jobs")
        phase = g.phase("Detail pages")
        phase.advance(1, detail="acme")  # silent
        phase.advance(1, detail="globex")
        phase.done("2 enriched, 1 skipped")
    out = buf.getvalue()
    assert "Detail pages: 2 enriched, 1 skipped" in out
    assert ANSI not in out


def test_line_mode_advance_and_start_are_silent():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        g = rp.group("Enriching jobs")
        phase = g.phase("Fit and notes")
        phase.start()
        phase.advance(1, detail="acme")
    # Nothing printed until done(); group/start/advance stay silent.
    assert buf.getvalue() == ""


def test_phase_default_summary_reports_count():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        phase = rp.group("Enriching jobs").phase("Company products")
        phase.advance(3)
        phase.done()
    assert "Company products: 3 done" in buf.getvalue()


def test_title_printed_on_enter():
    console, buf = _line_console()
    with RunProgress(console, tty=False, title="Job search run"):
        pass
    assert "Job search run" in buf.getvalue()


def test_tty_mode_prints_marker_legend():
    buf = io.StringIO()
    console = RichConsole(file=buf, force_terminal=True, color_system=None, width=80)
    with RunProgress(console, tty=True, title="Job search run"):
        pass
    out = buf.getvalue()
    assert "running" in out and "done" in out and "failed" in out


def test_status_marker_survives_narrow_terminal():
    """Marker lives in the label cell, so it isn't dropped on a narrow width."""
    from daily_driver.core.progress import Progress, _columns

    buf = io.StringIO()
    sink = RichConsole(file=buf, force_terminal=True, color_system=None, width=30)
    progress = Progress(*_columns(), console=sink, auto_refresh=False)
    rp = RunProgress(sink, tty=True)
    rp._progress = progress
    group = rp.group("Scraping sources")
    group.item("weworkremotely").finish(ok=False, detail="failed (timed out)")
    render = RichConsole(
        file=io.StringIO(), force_terminal=True, color_system=None, width=30
    )
    with render.capture() as cap:
        render.print(progress.get_renderable())
    # The failed marker is present even though the label is ellipsized.
    assert "x " in cap.get()


def test_tty_mode_starts_and_stops_progress():
    with RunProgress(_tty_console(), tty=True) as rp:
        assert rp._progress is not None
        g = rp.group("Scraping sources")
        item = g.item("apple")
        item.start()
        item.finish(True, "5 jobs")
        phase = g.phase("Detail pages")
        phase.advance(2, detail="acme")
        assert phase.completed == 2
    assert rp._progress is None  # stopped on exit


def test_tty_mode_stops_progress_on_exception():
    rp = RunProgress(_tty_console(), tty=True)
    with pytest.raises(RuntimeError):
        with rp:
            assert rp._progress is not None
            raise RuntimeError("boom")
    assert rp._progress is None  # teardown ran despite the exception


def test_group_header_counts_finished_children():
    """The group header total tracks children added; completed advances on finish."""
    with RunProgress(_tty_console(), tty=True) as rp:
        g = rp.group("Scraping sources")
        a, b = g.item("a"), g.item("b")
        header = g._progress._tasks[g._header_id]
        assert g._total == 2 and header.total == 2
        a.finish(True, "ok")
        assert header.completed == 1
        b.finish(False, "failed")
        assert header.completed == 2


def test_advance_matches_progress_callback_signature():
    """Phase.advance must be usable as a ProgressCallback (n, detail)."""
    group = Group(None, _line_console()[0], "x")
    phase = group.phase("y")
    cb = phase.advance
    cb(1, "acme")
    cb(1, None)
    assert phase.completed == 2
