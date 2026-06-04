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
from typing import Literal

from rich.logging import RichHandler

from daily_driver.core.console import Console

_handler: _LiveAwareRichHandler | None = None


class _LiveAwareRichHandler(RichHandler):
    """Rich log handler that can defer records past an active live display.

    While a live progress block owns the terminal, ``start_deferring()``
    buffers records instead of printing them (a raw log write would cut into
    the live region and commit half-frames to scrollback). ``flush_deferred()``
    replays the buffer below the stopped display, under a ``Warnings (N):``
    header when timestamps are off (normal mode).
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._show_time = bool(kwargs.get("show_time", True))
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._deferring = False
        self._buffer: list[stdlog.LogRecord] = []

    def emit(self, record: stdlog.LogRecord) -> None:
        if self._deferring:
            self._buffer.append(record)
        else:
            super().emit(record)

    def start_deferring(self) -> None:
        self._deferring = True

    def flush_deferred(self) -> None:
        self._deferring = False
        buffered, self._buffer = self._buffer, []
        if not buffered:
            return
        warnings = sum(1 for r in buffered if r.levelno >= stdlog.WARNING)
        if not self._show_time and warnings:
            self.console.print(f"\nWarnings ({warnings}):")
        for record in buffered:
            super().emit(record)


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
def deferred_logs(active: bool) -> Iterator[None]:
    """Buffer log records while a live display owns the terminal.

    When ``active``, records emitted inside the block are held and replayed on
    exit (including on exception), so they land below the stopped display
    rather than cutting into it. A no-op when inactive or before configure().
    """
    handler = _handler
    if not active or handler is None:
        yield
        return
    handler.start_deferring()
    try:
        yield
    finally:
        handler.flush_deferred()


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
