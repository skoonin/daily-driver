from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from daily_driver.integrations.notify import desktop_notify


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.csv"


def test_osascript_invoked_with_message(csv_path: Path) -> None:
    with patch("daily_driver.integrations.notify.shutil.which", return_value=None):
        with patch("daily_driver.integrations.notify.subprocess.run") as mock_run:
            desktop_notify("5 new jobs found", "Job Scraper", csv_path)

    mock_run.assert_called_once()
    argv = mock_run.call_args[0][0]
    assert argv[0] == "osascript"
    assert argv[1] == "-e"
    assert "5 new jobs found" in argv[2]


def test_terminal_notifier_preferred_when_available(csv_path: Path) -> None:
    with patch(
        "daily_driver.integrations.notify.shutil.which",
        return_value="/usr/local/bin/terminal-notifier",
    ):
        with patch("daily_driver.integrations.notify.subprocess.run") as mock_run:
            desktop_notify("3 new jobs found", "Job Scraper", csv_path)

    mock_run.assert_called_once()
    argv = mock_run.call_args[0][0]
    assert argv[0] == "terminal-notifier"


def test_osascript_failure_is_swallowed(csv_path: Path) -> None:
    with patch("daily_driver.integrations.notify.shutil.which", return_value=None):
        with patch(
            "daily_driver.integrations.notify.subprocess.run",
            side_effect=OSError("osascript not found"),
        ):
            # Must not propagate — notifications are best-effort.
            desktop_notify("1 new job", "Job Scraper", csv_path)
