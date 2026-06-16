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
