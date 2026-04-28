"""Log configuration for the daily_driver package.

Attaches a Rich handler to the `daily_driver` logger only — third-party
loggers are left untouched. Safe to call configure() multiple times;
prior handlers are removed before each setup so levels don't stack.
"""

from __future__ import annotations

import logging as stdlog
from typing import Literal

from rich.console import Console
from rich.logging import RichHandler


def configure(verbosity: Literal["quiet", "normal", "verbose"]) -> None:
    """Set up the daily_driver logger with a Rich stderr handler."""
    level_map: dict[str, int] = {
        "quiet": stdlog.WARNING,
        "normal": stdlog.INFO,
        "verbose": stdlog.DEBUG,
    }
    logger = stdlog.getLogger("daily_driver")

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    handler = RichHandler(
        console=Console(stderr=True),
        show_path=False,
        markup=True,
    )
    handler.setLevel(level_map[verbosity])
    logger.addHandler(handler)
    logger.setLevel(level_map[verbosity])
    logger.propagate = False


def get_logger(name: str) -> stdlog.Logger:
    """Return a child logger under the daily_driver namespace."""
    return stdlog.getLogger(f"daily_driver.{name}")
