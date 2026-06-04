"""Deferred-logging behaviour for the live-aware handler."""

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


def test_records_buffer_while_deferring_and_flush_below():
    buf, logger = _bind_handler(show_time=False)
    handler = logger.handlers[0]

    handler.start_deferring()
    logger.warning("[apple] unknown country code")
    # Nothing emitted while deferring.
    assert "unknown country code" not in buf.getvalue()

    handler.flush_deferred()
    out = buf.getvalue()
    assert "Warnings (1):" in out  # header only in no-timestamp (normal) mode
    assert "unknown country code" in out


def test_brackets_survive_without_markup():
    """markup=False keeps a bracketed source prefix like [apple] intact."""
    buf, logger = _bind_handler(show_time=False)
    logger.warning("[apple] search input not found")
    assert "[apple]" in buf.getvalue()


def test_non_deferred_emits_immediately():
    buf, logger = _bind_handler(show_time=False)
    logger.warning("[scraper] live line")
    assert "live line" in buf.getvalue()


def test_verbose_mode_has_no_warnings_header():
    """With timestamps on (-v/-vv), the flushed block omits the count header."""
    buf, logger = _bind_handler(show_time=True)
    handler = logger.handlers[0]
    handler.start_deferring()
    logger.warning("[apple] something")
    handler.flush_deferred()
    out = buf.getvalue()
    assert "Warnings (" not in out
    assert "something" in out
