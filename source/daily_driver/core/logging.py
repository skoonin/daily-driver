"""Log configuration for the daily_driver package.

Attaches a plain counting ``StreamHandler`` to the ``daily_driver`` logger,
bound to the stderr stream. The handler streams every record immediately and
tallies WARN+ records over a run so a terse ``Warnings: N`` line can close a run
in normal mode. Third-party logger families (e.g. JobSpy) can be rerouted
through the same handler so their warnings are counted and formatted alike. Safe
to call configure() multiple times; prior handlers are removed before each setup
so levels don't stack.
"""

from __future__ import annotations

import logging as stdlog
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import IO, Literal

from daily_driver.core.console import Console

_handler: _WarnCountingHandler | None = None

# Normal mode reads as a clean "WARNING <message>"; -v/-vv prefix a timestamp so
# a long run's stream is diagnosable. Matches the pre-enlighten format.
_PLAIN_FORMAT = "%(levelname)s %(message)s"
_TIMED_FORMAT = "%(asctime)s %(levelname)s %(message)s"
_TIME_DATEFMT = "%H:%M:%S"


class _WarnCountingHandler(stdlog.StreamHandler):  # type: ignore[type-arg]
    """Plain stderr handler that tallies warnings emitted under a live display.

    Records always emit immediately. While a live progress block owns the
    terminal, enlighten's scroll region carries these plain stderr writes above
    the pinned bars, so warnings surface as they happen instead of cutting in.
    ``start_counting()`` / ``stop_counting()`` tally WARN+ records over a run so
    a terse ``Warnings: N`` reconciliation line can close it in normal mode.
    """

    def __init__(self, stream: IO[str] | None = None, *, show_time: bool) -> None:
        super().__init__(stream)
        self._show_time = show_time
        self._counting = False
        self._warn_count = 0
        fmt = _TIMED_FORMAT if show_time else _PLAIN_FORMAT
        self.setFormatter(stdlog.Formatter(fmt, datefmt=_TIME_DATEFMT))

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
    """Set up the daily_driver logger with a plain counting stderr handler."""
    global _handler
    level_map: dict[str, int] = {
        "quiet": stdlog.ERROR,
        "normal": stdlog.WARNING,
        "verbose": stdlog.INFO,
        "debug": stdlog.DEBUG,
    }
    logger = stdlog.getLogger("daily_driver")

    for handler in logger.handlers[:]:
        if isinstance(handler, _WarnCountingHandler):
            logger.removeHandler(handler)

    # Bind the handler to the same stderr object the live display's enlighten
    # manager uses, so log lines and pinned bars interleave on one stream.
    handler = _WarnCountingHandler(
        stream=Console.get_log_console().file,
        show_time=verbosity in ("verbose", "debug"),
    )
    handler.setLevel(level_map[verbosity])
    logger.addHandler(handler)
    logger.setLevel(level_map[verbosity])
    logger.propagate = False
    _handler = handler


@contextmanager
def live_log_window(active: bool) -> Iterator[None]:
    """Stream logs live while a live display owns the terminal.

    Records emit immediately; enlighten's scroll region carries each one above
    the active bars, so problems surface as they happen rather than being held
    to the end. On exit (including on exception) a terse ``Warnings: N`` line
    reconciles the count in normal mode (no timestamps) -- the records
    themselves already scrolled above the block. A no-op when inactive or before
    configure().
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
            stream = handler.stream
            stream.write(f"\nWarnings: {count} (shown above)\n")
            stream.flush()


def adopt_third_party_loggers(prefix: str) -> None:
    """Route a third-party logger family through the daily_driver handler.

    Libraries like JobSpy attach their own ``StreamHandler`` -- bound to the
    real stderr at import time -- to each ``<prefix>:<site>`` logger. Replacing
    those handlers with our shared counting handler (and aligning each logger's
    level to the configured verbosity) makes their WARN+ lines count toward the
    run's ``Warnings: N`` total and read in our format, while silencing routine
    INFO chatter in normal mode. Call once, single-threaded, before any of those
    loggers emit. A no-op before ``configure()``.
    """
    handler = _handler
    if handler is None:
        return
    for name in list(stdlog.root.manager.loggerDict):
        if name == prefix or name.startswith(f"{prefix}:"):
            lib = stdlog.getLogger(name)
            for existing in lib.handlers[:]:
                lib.removeHandler(existing)
            lib.addHandler(handler)
            lib.setLevel(handler.level)
            lib.propagate = False


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
