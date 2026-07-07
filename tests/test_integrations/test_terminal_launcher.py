from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from daily_driver.integrations import terminal_launcher
from daily_driver.integrations.terminal_launcher import (
    TerminalLaunchError,
    open_in_terminal,
    shell_command,
)


def _ok(returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=returncode, stdout="", stderr=stderr
    )


def test_shell_command_quotes_arguments() -> None:
    assert (
        shell_command(
            ["/opt/dd bin/daily-driver", "check-in", "--workspace", "/tmp/w s"]
        )
        == "'/opt/dd bin/daily-driver' check-in --workspace '/tmp/w s'"
    )


def test_prefers_iterm_when_installed() -> None:
    with patch.object(terminal_launcher, "_iterm_installed", return_value=True):
        with patch(
            "daily_driver.integrations.terminal_launcher.subprocess.run",
            return_value=_ok(),
        ) as mock_run:
            open_in_terminal(["daily-driver", "day-start"])

    script = mock_run.call_args[0][0][2]
    assert 'tell application "iTerm"' in script
    assert "create tab with default profile" in script
    assert 'write text "daily-driver day-start"' in script


def test_falls_back_to_terminal_app() -> None:
    with patch.object(terminal_launcher, "_iterm_installed", return_value=False):
        with patch(
            "daily_driver.integrations.terminal_launcher.subprocess.run",
            return_value=_ok(),
        ) as mock_run:
            open_in_terminal(["daily-driver", "day-end"])

    script = mock_run.call_args[0][0][2]
    assert 'tell application "Terminal"' in script
    assert "do script" in script


def test_applescript_escaping_of_quotes_and_backslashes() -> None:
    with patch.object(terminal_launcher, "_iterm_installed", return_value=False):
        with patch(
            "daily_driver.integrations.terminal_launcher.subprocess.run",
            return_value=_ok(),
        ) as mock_run:
            open_in_terminal(["echo", 'a"b\\c'])

    script = mock_run.call_args[0][0][2]
    # shlex wraps the arg in single quotes; the embedded " and \ must be
    # AppleScript-escaped so the script stays syntactically valid.
    assert '\\"' in script
    assert "\\\\" in script


def test_nonzero_exit_raises_with_stderr_detail() -> None:
    with patch.object(terminal_launcher, "_iterm_installed", return_value=True):
        with patch(
            "daily_driver.integrations.terminal_launcher.subprocess.run",
            return_value=_ok(returncode=1, stderr="Not authorized (-1743)"),
        ):
            with pytest.raises(TerminalLaunchError, match="Not authorized"):
                open_in_terminal(["daily-driver", "day-start"])


def test_oserror_and_timeout_raise_launch_error() -> None:
    with patch.object(terminal_launcher, "_iterm_installed", return_value=True):
        with patch(
            "daily_driver.integrations.terminal_launcher.subprocess.run",
            side_effect=OSError("no osascript"),
        ):
            with pytest.raises(TerminalLaunchError):
                open_in_terminal(["daily-driver", "day-start"])
        with patch(
            "daily_driver.integrations.terminal_launcher.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=30),
        ):
            with pytest.raises(TerminalLaunchError):
                open_in_terminal(["daily-driver", "day-start"])
