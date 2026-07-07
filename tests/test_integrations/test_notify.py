from __future__ import annotations

import subprocess
from unittest.mock import patch

from daily_driver.integrations.notify import desktop_notify


def test_osascript_invoked_with_message() -> None:
    with patch("daily_driver.integrations.notify.shutil.which", return_value=None):
        with patch("daily_driver.integrations.notify.subprocess.run") as mock_run:
            desktop_notify("Job Scraper", "5 new jobs found", subtitle="jobs.csv")

    mock_run.assert_called_once()
    argv = mock_run.call_args[0][0]
    assert argv[0] == "osascript"
    assert argv[1] == "-e"
    assert "5 new jobs found" in argv[2]
    assert "jobs.csv" in argv[2]


def test_terminal_notifier_preferred_and_opens_url() -> None:
    with patch(
        "daily_driver.integrations.notify.shutil.which",
        return_value="/usr/local/bin/terminal-notifier",
    ):
        with patch("daily_driver.integrations.notify.subprocess.run") as mock_run:
            desktop_notify(
                "Job Scraper", "3 new jobs found", open_url="file:///tmp/jobs.csv"
            )

    mock_run.assert_called_once()
    argv = mock_run.call_args[0][0]
    assert argv[0] == "terminal-notifier"
    assert "-open" in argv
    assert argv[argv.index("-open") + 1] == "file:///tmp/jobs.csv"


def test_terminal_notifier_execute_runs_command_on_click() -> None:
    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch(
        "daily_driver.integrations.notify.shutil.which",
        return_value="/usr/local/bin/terminal-notifier",
    ):
        with patch(
            "daily_driver.integrations.notify.subprocess.run", return_value=ok
        ) as mock_run:
            clickable = desktop_notify(
                "Daily Driver", "Time for check-in", execute="daily-driver check-in"
            )

    assert clickable is True
    argv = mock_run.call_args[0][0]
    assert "-execute" in argv
    assert argv[argv.index("-execute") + 1] == "daily-driver check-in"


def test_terminal_notifier_nonzero_exit_reports_no_click() -> None:
    """A nonzero terminal-notifier exit means the notification was not delivered."""
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    with patch(
        "daily_driver.integrations.notify.shutil.which",
        return_value="/usr/local/bin/terminal-notifier",
    ):
        with patch(
            "daily_driver.integrations.notify.subprocess.run", return_value=fail
        ):
            clickable = desktop_notify("Daily Driver", "Test")

    assert clickable is False


def test_osascript_fallback_escapes_special_characters() -> None:
    """A double-quote or backslash in the message must not break the AppleScript."""
    with patch("daily_driver.integrations.notify.shutil.which", return_value=None):
        with patch("daily_driver.integrations.notify.subprocess.run") as mock_run:
            desktop_notify('Title with "quotes"', 'Message with \\ and "quotes"')

    script = mock_run.call_args[0][0][2]
    # The raw special chars must be escaped; unescaped " would break the script.
    assert '\\"' in script
    assert "\\\\" in script


def test_osascript_fallback_reports_no_click_support() -> None:
    """osascript renders the text but cannot run a command on click; callers
    with a click-critical flow need to know to degrade their message."""
    with patch("daily_driver.integrations.notify.shutil.which", return_value=None):
        with patch("daily_driver.integrations.notify.subprocess.run"):
            clickable = desktop_notify(
                "Daily Driver", "Time for check-in", execute="daily-driver check-in"
            )

    assert clickable is False


def test_failure_is_swallowed() -> None:
    with patch("daily_driver.integrations.notify.shutil.which", return_value=None):
        with patch(
            "daily_driver.integrations.notify.subprocess.run",
            side_effect=OSError("osascript not found"),
        ):
            # Must not propagate — notifications are best-effort.
            desktop_notify("Job Scraper", "1 new job")


def test_timeout_is_swallowed() -> None:
    """A wedged notifier helper must not hang or crash the caller."""
    with patch("daily_driver.integrations.notify.shutil.which", return_value=None):
        with patch(
            "daily_driver.integrations.notify.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=5),
        ):
            desktop_notify("Job Scraper", "1 new job")


def test_subprocess_calls_bound_by_timeout() -> None:
    with patch("daily_driver.integrations.notify.shutil.which", return_value=None):
        with patch("daily_driver.integrations.notify.subprocess.run") as mock_run:
            desktop_notify("Job Scraper", "1 new job")
    assert mock_run.call_args.kwargs.get("timeout") == 5
