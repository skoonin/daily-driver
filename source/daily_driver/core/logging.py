"""Log configuration for the daily_driver package.

Attaches a Rich handler to the `daily_driver` logger only — third-party
loggers are left untouched. Safe to call configure() multiple times;
prior handlers are removed before each setup so levels don't stack.
"""

from __future__ import annotations

import logging as stdlog
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal

from rich.logging import RichHandler

from daily_driver.core.console import Console

_handler: _LiveAwareRichHandler | None = None


class _LiveAwareRichHandler(RichHandler):
    """Rich log handler that tallies warnings emitted under a live display.

    Records always emit immediately. While a live progress block owns the
    terminal, Rich relocates each write above the region (thread-safe, via the
    live render lock), so warnings surface as they happen instead of cutting in.
    ``start_counting()`` / ``stop_counting()`` tally WARN+ records over a run so
    a terse ``Warnings: N`` reconciliation line can close it in normal mode.
    """

    def __init__(self, *args: Any, show_time: bool = True, **kwargs: Any) -> None:
        self._show_time = show_time
        super().__init__(*args, show_time=show_time, **kwargs)
        self._counting = False
        self._warn_count = 0

    def emit(self, record: stdlog.LogRecord) -> None:
        if self._counting and record.levelno >= stdlog.WARNING:
            self._warn_count += 1
        super().emit(record)

    def start_counting(self) -> None:
        self._counting = True
        self._warn_count = 0

    def stop_counting(self) -> int:
        self._counting = False
        return self._warn_count


def configure(verbosity: Literal["quiet", "normal", "verbose", "debug"]) -> None:
    """Set up the daily_driver logger with a Rich stderr handler."""
    global _handler
    level_map: dict[str, int] = {
        "quiet": stdlog.ERROR,
        "normal": stdlog.WARNING,
        "verbose": stdlog.INFO,
        "debug": stdlog.DEBUG,
    }
    logger = stdlog.getLogger("daily_driver")

    for handler in logger.handlers[:]:
        if isinstance(handler, RichHandler):
            logger.removeHandler(handler)

    # markup=False: scraper messages carry bracketed prefixes (e.g. "[apple]")
    # that Rich markup would otherwise parse away. Timestamps only from -v up,
    # so normal-mode warnings read as a clean "WARNING <message>".
    handler = _LiveAwareRichHandler(
        console=Console.get_log_console(),
        show_path=False,
        show_time=verbosity in ("verbose", "debug"),
        markup=False,
    )
    handler.setLevel(level_map[verbosity])
    logger.addHandler(handler)
    logger.setLevel(level_map[verbosity])
    logger.propagate = False
    _handler = handler


@contextmanager
def live_log_window(active: bool) -> Iterator[None]:
    """Stream logs live while a live display owns the terminal.

    Records emit immediately; Rich relocates each one above the active region,
    so problems surface as they happen rather than being held to the end. On
    exit (including on exception) a terse ``Warnings: N`` line reconciles the
    count in normal mode (no timestamps) -- the records themselves already
    scrolled above the block. A no-op when inactive or before configure().
    """
    handler = _handler
    if not active or handler is None:
        yield
        return
    handler.start_counting()
    try:
        yield
    finally:
        count = handler.stop_counting()
        if count and not handler._show_time:
            handler.console.print(f"\nWarnings: {count} (shown above)")


def get_logger(name: str) -> stdlog.Logger:
    """Return a child logger under the daily_driver namespace.

    Accepts either a bare short name (``"tracker"``) or a module ``__name__``
    that already carries the ``daily_driver`` prefix; the prefix is normalized
    so the resulting logger name is never doubled.
    """
    name = name.removeprefix("daily_driver.")
    return stdlog.getLogger(f"daily_driver.{name}")


def _fmt_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.isoformat() + " (naive)"
    return dt.isoformat()


def log_query_window(
    logger: stdlog.Logger, label: str, since: datetime, until: datetime
) -> None:
    """Emit a debug-level line describing the resolved gather window.

    Visible only at ``-vv`` (debug). Helps diagnose empty-result false
    negatives where the bug is in window math rather than data extraction.
    """
    logger.debug("%s: window since=%s until=%s", label, _fmt_dt(since), _fmt_dt(until))
