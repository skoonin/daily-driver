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
