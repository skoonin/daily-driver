"""Tests for the lean Console output class."""

from __future__ import annotations

import pytest
from rich.console import Console as RichConsole

from daily_driver.core.console import Console


@pytest.fixture(autouse=True)
def reset_console():
    """Reset Console class state between tests."""
    yield
    Console.setup_for_user(quiet=False, verbose=False, no_color=False)


def test_setup_defaults():
    Console.setup_for_user(quiet=False, verbose=False, no_color=False)
    assert Console.quiet_mode is False
    assert Console.verbose_mode is False
    assert Console._no_color is False


def test_setup_quiet():
    Console.setup_for_user(quiet=True, verbose=False, no_color=False)
    assert Console.quiet_mode is True
    assert Console.verbose_mode is False


def test_setup_verbose():
    Console.setup_for_user(quiet=False, verbose=True, no_color=False)
    assert Console.verbose_mode is True


def test_setup_no_color():
    Console.setup_for_user(quiet=False, verbose=False, no_color=True)
    assert Console._no_color is True


def test_error_always_shown_in_quiet_mode(capsys):
    """Console.error() must write to stderr even when quiet=True."""
    Console.setup_for_user(quiet=True, verbose=False, no_color=True)
    Console.error("something broke")
    captured = capsys.readouterr()
    assert "something broke" in captured.err
    assert captured.out == ""


def test_warning_always_shown_in_quiet_mode(capsys):
    """Console.warning() must write to stderr even when quiet=True."""
    Console.setup_for_user(quiet=True, verbose=False, no_color=True)
    Console.warning("heads up")
    captured = capsys.readouterr()
    assert "heads up" in captured.err
    assert captured.out == ""


def test_log_console_does_not_hard_wrap_when_captured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Captured stderr must not hard-wrap a long unbroken token.

    Regression: the log console used to hard-wrap at the detected width, which
    split path/command tokens mid-word (e.g. "jobs-last-run.json" ->
    "jobs-la\\nst-run.json") at certain widths, corrupting piped output and
    making width-dependent substring assertions flake. A narrow COLUMNS would
    fold the token here without soft-wrap.
    """
    monkeypatch.setenv("COLUMNS", "40")
    Console.setup_for_user(quiet=False, verbose=False, no_color=True)
    token = "averylongunbreakabletoken-jobs-last-run.json-1234567890"
    Console.warning(token)
    err = capsys.readouterr().err
    assert token in err


def test_get_log_console_returns_rich_console():
    lc = Console.get_log_console()
    assert isinstance(lc, RichConsole)


def test_get_user_console_returns_rich_console():
    uc = Console.get_user_console()
    assert isinstance(uc, RichConsole)


def test_log_console_is_stderr():
    lc = Console.get_log_console()
    assert lc.stderr is True


def test_user_console_is_stdout():
    uc = Console.get_user_console()
    assert uc.stderr is False


def test_live_progress_enabled_gates_on_tty_quiet_and_suppress(
    monkeypatch: pytest.MonkeyPatch,
):
    """The shared gate is is_tty() AND not quiet AND not suppress."""
    monkeypatch.setattr(Console, "is_tty", classmethod(lambda cls: True))

    Console.quiet_mode = False
    assert Console.live_progress_enabled() is True
    # --json (suppress) forces it off even on an animatable TTY.
    assert Console.live_progress_enabled(suppress=True) is False

    # Quiet mode forces it off.
    Console.quiet_mode = True
    assert Console.live_progress_enabled() is False
    Console.quiet_mode = False

    # A non-animatable stderr forces it off regardless of the other flags.
    monkeypatch.setattr(Console, "is_tty", classmethod(lambda cls: False))
    assert Console.live_progress_enabled() is False
