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

import json
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
        # color_system=None handles color suppression; markup and highlight
        # are orthogonal Rich features and stay on regardless of --no-color.
        color: Literal["auto"] | None = None if cls._no_color else "auto"
        theme = Theme(cls.THEME)

        cls._user_console = rich_console.Console(
            theme=theme,
            color_system=color,
            soft_wrap=is_piped,
        )
        cls._log_console = rich_console.Console(
            theme=theme,
            stderr=True,
            color_system=color,
        )

    @classmethod
    def get_log_console(cls) -> rich_console.Console:
        """Return the stderr console.

        Its underlying ``.file`` (stderr) is the shared stream that
        core/logging.py's handler and the live display's enlighten manager both
        bind, so log lines and pinned bars interleave on one channel.
        """
        if cls._log_console is None:
            cls._setup_consoles()
        assert cls._log_console is not None
        return cls._log_console

    @classmethod
    def is_tty(cls) -> bool:
        """Whether the stderr console can render an interactive live display.

        The live progress display targets stderr, so its capability — not
        stdout's — decides animated vs plain-line mode. Delegating to Rich's
        ``is_terminal`` (rather than a bare ``isatty()``) folds in the
        ``TERM=dumb`` and ``NO_COLOR``/``FORCE_COLOR`` cases where the fd is a
        TTY but Rich cannot animate it.
        """
        return cls.get_log_console().is_terminal

    @classmethod
    def live_progress_enabled(cls, *, suppress: bool = False) -> bool:
        """Whether a live progress display should render for long-running work.

        Single source of truth for the ``is_tty() and not quiet and not json``
        gate every progress call site shares: live bars animate only when stderr
        is an animatable TTY, quiet mode is off, and the caller is not
        suppressing live output (``suppress=True`` for ``--json``, which owns
        stdout and must stay free of progress frames).
        """
        return cls.is_tty() and not cls.quiet_mode and not suppress

    @classmethod
    def get_user_console(cls) -> rich_console.Console:
        """Return the stdout console."""
        if cls._user_console is None:
            cls._setup_consoles()
        assert cls._user_console is not None
        return cls._user_console

    @classmethod
    def emit_json(cls, data: object) -> None:
        """Print the ``{"schema": 1, "data": data}`` envelope to stdout.

        Single source of truth for every ``--json`` surface so the shape stays
        uniform: schema-1 envelope, ``indent=2``, and ``default=str`` so a
        ``date``/``Path`` in the payload serializes rather than raising
        ``TypeError``. A plain ``print`` (not the Rich console) keeps stdout a
        clean machine-readable JSON channel, independent of quiet mode.
        """
        print(json.dumps({"schema": 1, "data": data}, indent=2, default=str))

    # ------------------------------------------------------------------ #
    # Output methods
    # ------------------------------------------------------------------ #

    @classmethod
    def info(cls, message: str) -> None:
        """Print an informational status line to stderr. Suppressed in quiet mode."""
        if cls.quiet_mode:
            return
        cls.get_log_console().print(message, style="info")

    @classmethod
    def success(cls, message: str) -> None:
        """Print a success line to stderr. Suppressed in quiet mode."""
        if cls.quiet_mode:
            return
        cls.get_log_console().print(message, style="success")

    @classmethod
    def warning(cls, message: str) -> None:
        """Print a warning to stderr. Always visible."""
        cls.get_log_console().print(f"Warning: {message}", style="warning")

    @classmethod
    def error(cls, message: str) -> None:
        """Print an error to stderr. Always visible."""
        cls.get_log_console().print(f"Error: {message}", style="error")
