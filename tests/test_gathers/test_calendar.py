from __future__ import annotations

import subprocess
from datetime import datetime

from daily_driver.gathers.calendar import CalendarEvent, gather_events

_SINCE = datetime(2026, 4, 20, 0, 0, 0)
_UNTIL = datetime(2026, 4, 22, 23, 59, 59)

# Two-event output: first uses time-only lines (requires date context from ISO header),
# second uses full ISO datetime so the test doesn't depend on date-tracking logic.
_TWO_EVENT_OUTPUT = """\
Standup
    2026-04-20 09:30
    location: Zoom

Doctor Appointment
    2026-04-22 14:00
"""

_VALID_THEN_MALFORMED = """\
Team Sync
    2026-04-21 10:00

UNPARSEABLE BLOCK WITH NO DATE OR TIME AT ALL
"""


def _make_run_stub(stdout="", rc=0):
    def _run(args, **kw):
        return subprocess.CompletedProcess(
            args=args, returncode=rc, stdout=stdout, stderr=""
        )

    return _run


def test_gather_events_returns_empty_if_icalbuddy_missing(monkeypatch):
    monkeypatch.setattr("daily_driver.gathers.calendar.shutil.which", lambda _: None)
    called = []
    monkeypatch.setattr(
        "daily_driver.gathers.calendar.subprocess.run",
        lambda *a, **kw: called.append(a) or None,
    )

    result = gather_events(_SINCE, _UNTIL)

    assert result == []
    assert called == [], "subprocess.run should not be called when icalBuddy is missing"


def test_gather_events_parses_valid_output(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.gathers.calendar.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.gathers.calendar.subprocess.run",
        _make_run_stub(stdout=_TWO_EVENT_OUTPUT),
    )

    events = gather_events(_SINCE, _UNTIL)

    assert len(events) == 2
    titles = {e.title for e in events}
    assert "Standup" in titles
    assert "Doctor Appointment" in titles
    for e in events:
        assert isinstance(e, CalendarEvent)
        assert isinstance(e.start, datetime)


def test_gather_events_skips_malformed_block(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.gathers.calendar.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.gathers.calendar.subprocess.run",
        _make_run_stub(stdout=_VALID_THEN_MALFORMED),
    )

    events = gather_events(_SINCE, _UNTIL)

    assert len(events) == 1
    assert events[0].title == "Team Sync"


def test_gather_events_zero_results(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.gathers.calendar.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.gathers.calendar.subprocess.run",
        _make_run_stub(stdout=""),
    )

    result = gather_events(_SINCE, _UNTIL)

    assert result == []
