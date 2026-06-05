"""Live-window log behaviour for the live-aware handler.

Records always stream immediately (Rich relocates them above an active live
region); the handler only tallies WARN+ records so a terse ``Warnings (N):``
line can close a run in normal mode.
"""

from __future__ import annotations

import io
import logging

from rich.console import Console as RichConsole

from daily_driver.core import logging as ddlog


def _bind_handler(show_time: bool) -> tuple[io.StringIO, logging.Logger]:
    buf = io.StringIO()
    console = RichConsole(file=buf, force_terminal=False, color_system=None, width=200)
    handler = ddlog._LiveAwareRichHandler(
        console=console, show_path=False, show_time=show_time, markup=False
    )
    handler.setLevel(logging.WARNING)
    logger = logging.getLogger("daily_driver.test_defer")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    return buf, logger


def test_records_stream_live_while_counting():
    """Records emit immediately while the live window is active (no buffering)."""
    buf, logger = _bind_handler(show_time=False)
    handler = logger.handlers[0]

    handler.start_counting()
    logger.warning("[apple] unknown country code")
    # Streamed live, not held.
    assert "unknown country code" in buf.getvalue()

    count = handler.stop_counting()
    assert count == 1


def test_stop_counting_tallies_only_warnings():
    """INFO records reach the live stream but don't count toward the warning total."""
    buf, logger = _bind_handler(show_time=False)
    handler = logger.handlers[0]
    handler.setLevel(logging.INFO)
    logger.setLevel(logging.INFO)

    handler.start_counting()
    logger.info("[scraper] heartbeat")
    logger.warning("[apple] one problem")
    assert "heartbeat" in buf.getvalue()
    assert handler.stop_counting() == 1


def test_brackets_survive_without_markup():
    """markup=False keeps a bracketed source prefix like [apple] intact."""
    buf, logger = _bind_handler(show_time=False)
    logger.warning("[apple] search input not found")
    assert "[apple]" in buf.getvalue()


def test_live_log_window_prints_count_in_normal_mode():
    """Normal mode (no timestamps): the window closes with a Warnings (N): line."""
    buf, logger = _bind_handler(show_time=False)
    handler = logger.handlers[0]
    ddlog._handler = handler
    try:
        with ddlog.live_log_window(active=True):
            logger.warning("[apple] something")
        out = buf.getvalue()
        assert "something" in out  # streamed live
        assert "Warnings: 1 (shown above)" in out  # terse end-of-run count
    finally:
        ddlog._handler = None


def test_live_log_window_no_count_in_verbose_mode():
    """With timestamps on (-v/-vv), no trailing count line — the stream stands alone."""
    buf, logger = _bind_handler(show_time=True)
    handler = logger.handlers[0]
    ddlog._handler = handler
    try:
        with ddlog.live_log_window(active=True):
            logger.warning("[apple] something")
        out = buf.getvalue()
        assert "Warnings:" not in out
        assert "something" in out
    finally:
        ddlog._handler = None


def test_live_log_window_noop_when_inactive():
    """Inactive window is a pure no-op: no count line even with warnings."""
    buf, logger = _bind_handler(show_time=False)
    handler = logger.handlers[0]
    ddlog._handler = handler
    try:
        with ddlog.live_log_window(active=False):
            logger.warning("[apple] something")
        out = buf.getvalue()
        assert "something" in out
        assert "Warnings:" not in out
    finally:
        ddlog._handler = None


def test_adopt_third_party_loggers_reroutes_and_aligns_level():
    """A third-party logger's own stderr handler is replaced by ours, its level
    aligned to ours, and propagation disabled -- so its lines route above the
    live block instead of bypassing Rich's redirect."""
    own_buf = io.StringIO()
    third_party = logging.getLogger("FakeLib:SiteA")
    third_party.handlers.clear()
    own_handler = logging.StreamHandler(own_buf)
    third_party.addHandler(own_handler)
    third_party.setLevel(logging.INFO)
    third_party.propagate = True

    buf, our_logger = _bind_handler(show_time=False)
    our_handler = our_logger.handlers[0]  # level WARNING from _bind_handler
    ddlog._handler = our_handler
    try:
        ddlog.adopt_third_party_loggers("FakeLib")

        assert third_party.handlers == [our_handler]
        assert third_party.level == our_handler.level  # WARNING -> INFO suppressed
        assert third_party.propagate is False

        # An INFO line is now filtered (our level is WARNING); a WARNING routes
        # through our handler's buffer, not the library's own stderr handler.
        third_party.info("chatty progress")
        third_party.warning("real problem")
        assert "chatty progress" not in buf.getvalue()
        assert "real problem" in buf.getvalue()
        assert own_buf.getvalue() == ""
    finally:
        ddlog._handler = None
        third_party.handlers.clear()
