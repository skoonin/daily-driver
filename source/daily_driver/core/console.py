"""Centralized console output management using Rich.

Two streams:
  _user_console  → stdout  — data and user-facing text; gated by quiet_mode
  _log_console   → stderr  — status, diagnostics, warnings, errors

Configure once per process via ``setup_for_user()``. All command files should
import and use the class-level methods rather than bare ``print()``.

JSON/structured data payloads that belong on stdout should still use plain
``print()`` directly — those are process stdout contracts, not console output.
"""

from __future__ import annotations

import sys
from typing import Literal

import rich.console as rich_console
from rich.theme import Theme


class Console:
    """Slim two-stream Rich console for daily-driver.

    User output (stdout) is gated by quiet/verbose flags.
    Diagnostic output (stderr) is always available for warnings and errors.
    """

    _user_console: rich_console.Console | None = None
    _log_console: rich_console.Console | None = None

    quiet_mode: bool = False
    verbose_mode: bool = False
    _no_color: bool = False

    THEME = {
        "info": "bright_green",
        "success": "green bold",
        "warning": "bright_yellow",
        "error": "red bold",
        "debug": "bright_blue",
    }

    @classmethod
    def setup_for_user(
        cls,
        quiet: bool = False,
        verbose: bool = False,
        no_color: bool = False,
    ) -> None:
        """Configure output channels from parsed CLI flags.

        Call once in ``_common.configure()`` after the logger is set up.
        """
        cls.quiet_mode = quiet
        cls.verbose_mode = verbose
        cls._no_color = no_color
        cls._setup_consoles()

    @classmethod
    def _setup_consoles(cls) -> None:
        is_piped = not sys.stdout.isatty()
        color: Literal["auto"] | None = None if cls._no_color else "auto"
        theme = Theme(cls.THEME)

        cls._user_console = rich_console.Console(
            theme=theme,
            color_system=color,
            highlight=not cls._no_color,
            soft_wrap=is_piped,
        )
        cls._log_console = rich_console.Console(
            theme=theme,
            stderr=True,
            color_system=color,
            highlight=not cls._no_color,
        )

    @classmethod
    def get_log_console(cls) -> rich_console.Console:
        """Return the stderr console; used by core/logging.py RichHandler."""
        if cls._log_console is None:
            cls._setup_consoles()
        assert cls._log_console is not None
        return cls._log_console

    @classmethod
    def get_user_console(cls) -> rich_console.Console:
        """Return the stdout console."""
        if cls._user_console is None:
            cls._setup_consoles()
        assert cls._user_console is not None
        return cls._user_console

    # ------------------------------------------------------------------ #
    # Output methods
    # ------------------------------------------------------------------ #

    @classmethod
    def print(cls, message: object, style: str | None = None) -> None:
        """Print to stdout. Suppressed in quiet mode."""
        if cls.quiet_mode:
            return
        cls.get_user_console().print(message, style=style, markup=not cls._no_color)

    @classmethod
    def info(cls, message: str) -> None:
        """Print an informational status line to stderr. Suppressed in quiet mode."""
        if cls.quiet_mode:
            return
        cls.get_log_console().print(message, style="info", markup=not cls._no_color)

    @classmethod
    def success(cls, message: str) -> None:
        """Print a success line to stderr. Suppressed in quiet mode."""
        if cls.quiet_mode:
            return
        cls.get_log_console().print(message, style="success", markup=not cls._no_color)

    @classmethod
    def warning(cls, message: str) -> None:
        """Print a warning to stderr. Always visible."""
        cls.get_log_console().print(
            f"Warning: {message}", style="warning", markup=not cls._no_color
        )

    @classmethod
    def error(cls, message: str) -> None:
        """Print an error to stderr. Always visible."""
        cls.get_log_console().print(
            f"Error: {message}", style="error", markup=not cls._no_color
        )

    @classmethod
    def debug(cls, message: str) -> None:
        """Print a debug line to stderr. Only shown when verbose_mode is True."""
        if not cls.verbose_mode:
            return
        cls.get_log_console().print(
            f"DEBUG: {message}", style="debug", markup=not cls._no_color
        )
