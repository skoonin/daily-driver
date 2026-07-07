"""Tests for the phased live-progress facade.

Plain-mode tests assert on visible buffer text only. Live-mode tests drive an
injected enlighten ``Manager`` bound to a buffer (a real manager renders its rows
to the buffer but emits no ANSI on a non-TTY stream) and additionally inspect the
facade's counter state (``_bar.count``/``_failed.count``) where the coloured
ok-vs-failed segment logic has no distinguishable signature in the plain buffer.
"""

from __future__ import annotations

import io
import threading
import time

import enlighten
import pytest
from rich.console import Console as RichConsole

from daily_driver.core.console import Console
from daily_driver.core.progress import RunProgress

ANSI = "\x1b["
BAR_GLYPH = "█"  # enlighten fill-bar block


def _line_console() -> tuple[RichConsole, io.StringIO]:
    """A non-TTY Rich console capturing to a buffer (plain-mode target)."""
    buf = io.StringIO()
    console = RichConsole(file=buf, force_terminal=False, color_system=None)
    return console, buf


def _inject_live(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[io.StringIO, enlighten.Manager]:
    """Force live mode with a manager that renders to a buffer.

    Patches ``_start_live`` so the cursor-query probe (which a buffer can never
    answer) is bypassed and ``tty=True`` yields a working manager.
    """
    buf = io.StringIO()
    # Fixed width so the fill bar renders deterministically (a buffer has no
    # terminal width to query). Colour is gated on Console._no_color, so force
    # colour on to exercise the coloured path.
    Console._no_color = False
    manager = enlighten.Manager(stream=buf, enabled=True, width=100)
    monkeypatch.setattr(RunProgress, "_start_live", lambda self: manager)
    return buf, manager


# ----------------------------------------------------------------------- #
# Plain (non-TTY) mode
# ----------------------------------------------------------------------- #


def test_line_mode_item_prints_on_finish_no_ansi():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        g = rp.group("Scraping sources")
        item = g.item("apple")
        item.start()  # silent in plain mode
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


def test_group_done_prints_summary_in_plain_mode():
    console, buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        g = rp.group("Scraping sources")
        g.item("apple").finish(ok=True, detail="5 jobs")
        g.done("5 found, 5 new")
    assert "Scraping sources: 5 found, 5 new" in buf.getvalue()


def test_quiet_mode_suppresses_plain_output(monkeypatch):
    """Quiet ("errors only") drops the title, per-source lines, and note() --
    only warnings/errors (handled elsewhere) should surface."""
    monkeypatch.setattr(Console, "quiet_mode", True)
    console, buf = _line_console()
    with RunProgress(console, tty=False, title="Job search run") as rp:
        rp.note("12 searches per site")
        g = rp.group("Scraping sources")
        g.item("apple").finish(ok=True, detail="5 jobs")
        g.done("5 found, 5 new")
    assert buf.getvalue() == ""


def test_title_printed_on_enter():
    console, buf = _line_console()
    with RunProgress(console, tty=False, title="Job search run"):
        pass
    assert "Job search run" in buf.getvalue()


def test_advance_matches_progress_callback_signature():
    """Phase.advance must be usable as a ProgressCallback (n, detail)."""
    console, _buf = _line_console()
    with RunProgress(console, tty=False) as rp:
        phase = rp.group("Enriching jobs").phase("Fit and notes")
        cb = phase.advance
        cb(1, "acme")
        cb(1, None)
        assert phase.completed == 2


# ----------------------------------------------------------------------- #
# Live (enlighten) mode
# ----------------------------------------------------------------------- #


def test_live_mode_item_persists_result_in_bar_on_finish(monkeypatch):
    """A finished source's bar persists at 100% with the result folded into its
    label (one counter, mutated in place -- not closed/recreated)."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        item = rp.group("Scraping sources").item("apple")
        item.start()
        item.finish(ok=True, detail="128 jobs")
        assert item._bar is not None
        assert "128 jobs" in item._bar.desc  # result folded into the label
        assert item._bar.count == item._bar.total  # filled to 100%
    out = buf.getvalue()
    assert "apple" in out
    assert "128 jobs" in out


def test_live_mode_group_header_counts_finished_children(monkeypatch):
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        g = rp.group("Scraping sources")
        a, b = g.item("a"), g.item("b")
        assert g._total == 2
        a.finish(ok=True, detail="ok")
        assert g._completed == 1
        b.finish(ok=False, detail="failed")
        assert g._completed == 2
    # The live count text reached the stream at least once.
    assert "Scraping sources" in buf.getvalue()


def test_live_mode_group_header_renders_fill_bar(monkeypatch):
    """The group header is a progress bar (counter with a total), so its fill
    bar glyph reaches the stream as sources finish."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        g = rp.group("Scraping sources")
        a, b = g.item("a"), g.item("b")
        a.start()
        a.finish(ok=True, detail="ok")
        b.finish(ok=True, detail="ok")
    assert BAR_GLYPH in buf.getvalue()


def test_live_mode_phase_renders_fill_bar(monkeypatch):
    """A phase created with a total renders a fill bar as it advances."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        phase = rp.group("Enriching jobs").phase("Detail pages", total=4)
        phase.start()
        for _ in range(4):
            phase.advance(1)
    assert BAR_GLYPH in buf.getvalue()


def test_live_mode_phase_advance_detail_shows_in_bar(monkeypatch):
    """Phase.advance(detail=...) folds the current item (e.g. company) into the
    bar label so a long phase shows live which work it is doing."""
    _buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        phase = rp.group("Enriching jobs").phase("Fit and notes", total=4)
        phase.start()
        phase.advance(1, detail="Acme")
        assert "Fit and notes" in phase._bar.desc
        assert "Acme" in phase._bar.desc
        phase.advance(1, detail="Globex")
        assert "Globex" in phase._bar.desc


def test_live_mode_phase_set_total_rebases_denominator(monkeypatch):
    """set_total re-bases a phase pinned with an upper-bound total once the
    planner resolves the real (e.g. budget-capped) work count."""
    _buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        phase = rp.group("Enriching jobs").phase("Fit and notes", total=100)
        phase.start()
        phase.set_total(7)
        assert phase._bar.total == 7
        # Never below what has already completed (and never zero).
        phase.advance(3)
        phase.set_total(1)
        assert phase._bar.total == 3
        phase.set_total(0)
        assert phase._bar.total == 3


def test_live_mode_failed_source_uses_red_subcounter(monkeypatch):
    """A failed source advances the header's red subcounter, not the green
    main counter -- so ok vs failed show as coloured segments."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        g = rp.group("Scraping sources")
        ok, bad = g.item("ok_src"), g.item("bad_src")
        ok.finish(ok=True, detail="5 found")
        bad.finish(ok=False, detail="failed (timed out)")
        assert g._failed is not None
        # enlighten: the main counter's count is the aggregate; the subcounter
        # tracks its own (red) segment, so green = count - failed.count.
        assert g._failed.count == 1  # the failed source landed on the red segment
        assert g._bar.count == 2  # aggregate (1 green + 1 red)
        assert g._bar.count - g._failed.count == 1  # green (ok) segment
        assert g._completed == 2


def test_live_mode_title_pinned_as_status_bar(monkeypatch):
    """In live mode the title is a pinned status bar (top of the bottom block),
    not an ordinary line above the scroll region -- so no terminal-height gap
    opens between the title and the bars. It seats lazily, on the first counter
    (here, the group header), so the whole block sets the scroll region once."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True, title="Job search run") as rp:
        rp.group("Scraping sources").item("apple")  # first counter seats the title
        assert rp._title_bar is not None  # pinned, not plain-printed
    assert "Job search run" in buf.getvalue()


def test_live_mode_empty_run_still_shows_title(monkeypatch):
    """A run that pins no counters still shows its title (seated at teardown)."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True, title="Job search run") as rp:
        assert rp._title_bar is None  # not seated until a counter or teardown
    assert rp._title_bar is not None
    assert "Job search run" in buf.getvalue()


def test_reserve_sets_scroll_region_once(monkeypatch):
    """reserve() pre-sizes the block so adding bars does not keep resizing the
    scroll region: a single CSI r (change-scroll-region) reaches the stream for
    the whole block instead of one per bar."""
    buf, _mgr = _inject_live(monkeypatch)
    # A buffer-bound manager renders no ANSI, so drive a real TTY-shaped term to
    # observe the scroll-region sequence. Count region sets via the manager's own
    # accounting instead: each grow bumps scroll_offset, so a single reserve to
    # the final size means scroll_offset never grows again as bars are added.
    console, _ = _line_console()
    with RunProgress(console, tty=True, title="Job search run") as rp:
        rp.reserve(2 + 3)  # title + header + 3 sources
        offset_after_reserve = _mgr.scroll_offset
        g = rp.group("Scraping sources")
        for name in ("a", "b", "c"):
            g.item(name).start()
        # Bars fit inside the reserved region; the region never grew again.
        assert _mgr.scroll_offset == offset_after_reserve
    assert "Job search run" in buf.getvalue()


def test_reserve_is_idempotent_and_noop_after_first(monkeypatch):
    """A second reserve() is a no-op (block already seated)."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True, title="Job search run") as rp:
        rp.reserve(5)
        first = _mgr.scroll_offset
        rp.reserve(20)  # ignored -- already reserved
        assert _mgr.scroll_offset == first


def test_reserve_noop_in_plain_mode():
    """reserve() does nothing in plain mode and the title still prints."""
    console, buf = _line_console()
    with RunProgress(console, tty=False, title="Job search run") as rp:
        rp.reserve(8)  # must not raise
        rp.group("Scraping sources").item("apple").finish(ok=True, detail="5 jobs")
    out = buf.getvalue()
    assert "Job search run" in out
    assert "apple: 5 jobs" in out


def test_live_mode_show_breakdown_stacks_colored_segments(monkeypatch):
    """A finished bar re-bases into stacked subcounters summing to the total --
    the enlighten multicolored idiom (base + add_subcounter)."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        item = rp.group("Scraping sources").item("linkedin")
        item.start()
        item.finish(ok=True, detail="61 found")
        item.show_breakdown([(40, "green"), (15, "blue"), (6, "yellow")])
        assert item._bar is not None
        assert item._bar.total == 61  # total re-based to the segment sum
        assert item._bar.count == 61  # subcounters advance the parent back to full
        assert len(item._bar._subcounters) == 3  # one per non-zero segment
    assert BAR_GLYPH in buf.getvalue()


def test_show_breakdown_skips_zero_segments(monkeypatch):
    """Zero-count segments are dropped so empty colours don't clutter the bar."""
    _buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        item = rp.group("Scraping sources").item("apple")
        item.start()
        item.finish(ok=True, detail="5 found")
        item.show_breakdown([(5, "green"), (0, "blue"), (0, "yellow")])
        assert item._bar is not None
        assert len(item._bar._subcounters) == 1
        assert item._bar.total == 5


def test_live_mode_slow_source_note_shown(monkeypatch):
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        item = rp.group("Scraping sources").item("linkedin")
        item.start(note="running -- can take several minutes")
    assert "can take several minutes" in buf.getvalue()


def test_live_mode_item_progress_renders_fill_bar(monkeypatch):
    """A source fed page progress fills its bar to the page total."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        item = rp.group("Scraping sources").item("linkedin")
        item.start(note="running -- can take several minutes")
        for page in range(1, 7):
            item.progress(page, 6)
        assert item._bar.count == 6
        assert item._bar.total == 6
    out = buf.getvalue()
    assert BAR_GLYPH in out and "linkedin" in out


def test_live_mode_item_progress_counts_up_without_total(monkeypatch):
    """With no reliable total, progress just advances the count (count-up)."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    with RunProgress(console, tty=True) as rp:
        item = rp.group("Scraping sources").item("indeed")
        item.start()
        item.progress(1, None)
        item.progress(2, None)
        assert item._bar.count == 2
    assert "indeed" in buf.getvalue()


def test_item_progress_after_close_is_a_noop(monkeypatch):
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    rp = RunProgress(console, tty=True)
    with rp:
        item = rp.group("Scraping sources").item("linkedin")
        item.start()
    before = buf.getvalue()
    item.progress(3, 6)  # late page event after teardown -> no-op
    assert buf.getvalue() == before


def test_live_mode_teardown_stops_manager_on_exception(monkeypatch):
    _buf, manager = _inject_live(monkeypatch)
    console, _ = _line_console()
    rp = RunProgress(console, tty=True)
    with pytest.raises(RuntimeError):
        with rp:
            assert rp._manager is manager
            raise RuntimeError("boom")
    # Teardown ran despite the exception: manager cleared and run marked closed.
    assert rp._manager is None
    assert rp._closed is True


def test_exit_seats_title_failure_still_stops_and_preserves_exception(monkeypatch):
    """If seating the pending title raises during __exit__ (e.g. stderr closing
    on a SIGTERM unwind), the manager is still stopped (scroll region/cursor
    restored) and the in-flight exception is not replaced by the seat failure."""

    stopped = []

    class _FakeManager:
        enabled = True

        def status_bar(self, *args, **kwargs):  # seating the title fails
            raise OSError("stderr closed")

        def stop(self):
            stopped.append(True)

    fake = _FakeManager()
    monkeypatch.setattr(RunProgress, "_start_live", lambda self: fake)
    console, _ = _line_console()
    rp = RunProgress(console, tty=True, title="Job search run")

    # No counter is created, so the title is still pending when __exit__ tries to
    # seat it. The body's KeyboardInterrupt must survive the seat OSError.
    with pytest.raises(KeyboardInterrupt):
        with rp:
            raise KeyboardInterrupt()

    assert stopped == [True]  # stop() ran despite the seating failure
    assert rp._manager is None
    assert rp._closed is True


def test_finish_after_close_is_a_noop(monkeypatch):
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    rp = RunProgress(console, tty=True)
    with rp:
        item = rp.group("Scraping sources").item("apple")
        item.start()
    # A worker can still call finish() after Ctrl-C teardown (pool not waited on).
    before = buf.getvalue()
    item.finish(ok=True, detail="late result")  # must not raise or emit
    assert buf.getvalue() == before
    assert "late result" not in buf.getvalue()


def test_concurrent_finish_from_threads_counts_each_once(monkeypatch):
    """Many workers finishing concurrently: the single lock keeps the group's
    finished-child count exact and no enlighten call corrupts state."""
    buf, _mgr = _inject_live(monkeypatch)
    console, _ = _line_console()
    n = 24
    with RunProgress(console, tty=True) as rp:
        g = rp.group("Scraping sources")
        items = [g.item(f"src{i}") for i in range(n)]
        barrier = threading.Barrier(n)

        def run(item):
            barrier.wait()  # maximize contention
            item.start()
            item.finish(ok=True, detail="done")

        threads = [threading.Thread(target=run, args=(it,)) for it in items]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert g._completed == n


def test_unresponsive_tty_falls_back_to_plain_within_timeout(monkeypatch):
    """A TTY that never answers the cursor query downgrades to plain mode in
    __enter__ rather than hanging; later rows then print plain lines."""

    class _FakeTerm:
        def get_location(self, timeout=1.0):
            return (-1, -1)  # never answers

    class _FakeManager:
        enabled = True
        term = _FakeTerm()

        def stop(self):  # pragma: no cover - not reached (manager discarded)
            pass

    monkeypatch.setattr(enlighten, "get_manager", lambda stream=None: _FakeManager())
    console, buf = _line_console()

    start = time.perf_counter()
    with RunProgress(console, tty=True) as rp:
        assert rp._manager is None  # downgraded to plain
        rp.group("Scraping sources").item("apple").finish(ok=True, detail="5 jobs")
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0  # no hang
    assert "apple: 5 jobs" in buf.getvalue()  # plain output
    assert ANSI not in buf.getvalue()


def test_live_mode_shadows_cursor_query_after_startup_probe(monkeypatch):
    """A responsive terminal is probed exactly once. After that, the terminal's
    get_location is shadowed to a constant so enlighten's per-write scroll-area
    maintenance never emits another ESC[6n -- a late reply on a slow terminal
    would echo as ^[[row;colR garbage mid-run."""

    class _FakeTerm:
        def __init__(self):
            self.queries = 0

        def get_location(self, timeout=1.0):
            self.queries += 1
            return (5, 0)

    class _FakeManager:
        enabled = True

        def __init__(self):
            self.term = _FakeTerm()

        def stop(self):
            pass

    fake = _FakeManager()
    real_term = fake.term  # the shadow replaces the bound attribute; keep a handle

    monkeypatch.setattr(enlighten, "get_manager", lambda stream=None: fake)
    console, _buf = _line_console()
    with RunProgress(console, tty=True) as rp:
        assert rp._manager is fake  # probe passed; live mode
        assert real_term.queries == 1  # startup probe only
        # enlighten's _set_scroll_area path calls term.get_location on every
        # bar write; the shadow must answer without a real query.
        assert fake.term.get_location(timeout=1) == (0, 0)
        assert real_term.queries == 1  # untouched by the shadow
