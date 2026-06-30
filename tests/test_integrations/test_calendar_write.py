from __future__ import annotations

import subprocess
from datetime import date
from unittest.mock import patch

from daily_driver.core.plan import CalendarEvent
from daily_driver.integrations import calendar_write

_DAY = date(2026, 6, 30)
_EVENTS = [
    CalendarEvent(
        title='Evil "; tell application "Finder" to quit',
        start_seconds=36000,
        end_seconds=37800,
        notes="daily-plan:2026-06-30",
    )
]


def _patch_darwin_with_osascript():
    return (
        patch("daily_driver.integrations.calendar_write.sys.platform", "darwin"),
        patch(
            "daily_driver.integrations.calendar_write.shutil.which",
            return_value="/usr/bin/osascript",
        ),
    )


def test_event_strings_passed_as_argv_not_interpolated() -> None:
    plat, which = _patch_darwin_with_osascript()
    with (
        plat,
        which,
        patch("daily_driver.integrations.calendar_write.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        result = calendar_write.write_day("Daily Plan", _DAY, _EVENTS)

    mock_run.assert_called_once()
    argv = mock_run.call_args[0][0]
    assert argv[0] == "osascript"
    assert argv[1] == "-"
    # The injection-laden title rides in argv, never in the script body.
    assert _EVENTS[0].title in argv
    assert "Daily Plan" in argv
    assert "daily-plan:2026-06-30" in argv
    script_body = mock_run.call_args.kwargs["input"]
    assert _EVENTS[0].title not in script_body
    assert "Daily Plan" not in script_body
    assert "on run argv" in script_body
    assert result.ok is True
    assert result.written == 1


def test_idempotent_tag_passed_for_delete() -> None:
    """The day's tag is in argv so AppleScript can delete-then-write."""
    plat, which = _patch_darwin_with_osascript()
    with (
        plat,
        which,
        patch("daily_driver.integrations.calendar_write.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        calendar_write.write_day("Daily Plan", _DAY, _EVENTS)

    argv = mock_run.call_args[0][0]
    # argv layout: osascript, -, calName, tag, count, ...
    assert argv[3] == "daily-plan:2026-06-30"
    assert argv[4] == "1"


def test_subprocess_bound_by_timeout_and_check_false() -> None:
    plat, which = _patch_darwin_with_osascript()
    with (
        plat,
        which,
        patch("daily_driver.integrations.calendar_write.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        calendar_write.write_day("Daily Plan", _DAY, _EVENTS)

    kwargs = mock_run.call_args.kwargs
    assert kwargs.get("timeout") == 30
    assert kwargs.get("check") is False


def test_oserror_is_swallowed() -> None:
    plat, which = _patch_darwin_with_osascript()
    with (
        plat,
        which,
        patch(
            "daily_driver.integrations.calendar_write.subprocess.run",
            side_effect=OSError("osascript not found"),
        ),
    ):
        result = calendar_write.write_day("Daily Plan", _DAY, _EVENTS)
    assert result.ok is False
    assert result.written == 0


def test_timeout_is_swallowed() -> None:
    plat, which = _patch_darwin_with_osascript()
    with (
        plat,
        which,
        patch(
            "daily_driver.integrations.calendar_write.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=30),
        ),
    ):
        result = calendar_write.write_day("Daily Plan", _DAY, _EVENTS)
    assert result.ok is False


def test_nonzero_exit_is_non_fatal() -> None:
    plat, which = _patch_darwin_with_osascript()
    with (
        plat,
        which,
        patch("daily_driver.integrations.calendar_write.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "no calendar")
        result = calendar_write.write_day("Daily Plan", _DAY, _EVENTS)
    assert result.ok is False
    assert "no calendar" in (result.reason or "")


def test_non_darwin_is_clean_no_op() -> None:
    with (
        patch("daily_driver.integrations.calendar_write.sys.platform", "linux"),
        patch("daily_driver.integrations.calendar_write.subprocess.run") as mock_run,
    ):
        result = calendar_write.write_day("Daily Plan", _DAY, _EVENTS)
    mock_run.assert_not_called()
    assert result.ok is False


def test_missing_osascript_is_clean_no_op() -> None:
    with (
        patch("daily_driver.integrations.calendar_write.sys.platform", "darwin"),
        patch(
            "daily_driver.integrations.calendar_write.shutil.which", return_value=None
        ),
        patch("daily_driver.integrations.calendar_write.subprocess.run") as mock_run,
    ):
        result = calendar_write.write_day("Daily Plan", _DAY, _EVENTS)
    mock_run.assert_not_called()
    assert result.ok is False
