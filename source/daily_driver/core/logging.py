"""Log configuration for the daily_driver package.

Attaches a Rich handler to the `daily_driver` logger only — third-party
loggers are left untouched. Safe to call configure() multiple times;
prior handlers are removed before each setup so levels don't stack.
"""

from __future__ import annotations

import logging as stdlog
from datetime import datetime
from typing import Literal

from rich.logging import RichHandler

from daily_driver.core.console import Console


def configure(verbosity: Literal["quiet", "normal", "verbose", "debug"]) -> None:
    """Set up the daily_driver logger with a Rich stderr handler."""
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

    handler = RichHandler(
        console=Console.get_log_console(),
        show_path=False,
        markup=True,
    )
    handler.setLevel(level_map[verbosity])
    logger.addHandler(handler)
    logger.setLevel(level_map[verbosity])
    logger.propagate = False


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
