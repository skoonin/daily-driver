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

# Real icalBuddy 1.10.x format with the app's flags: title at column 0,
# properties indented, and crucially NO blank line between events. The previous
# blank-line splitter collapsed all of these into a single block (1 event). The
# third event carries a wrapped multi-line location (indented continuation, not
# blank-separated) — the Gap 6 cause-2 shape, which must stay part of one event.
_REAL_FORMAT_NO_BLANKS = """\
Standup
    2026-04-20 09:30
    location: Zoom
Doctor Appointment
    2026-04-22 14:00
Onsite Interview
    2026-04-23 11:00
    location: 1234 Long Street, Suite 500,
    Big City, Province
"""

# All-day event: a bare ISO date line with no time. Should anchor start at midnight.
_ALL_DAY_OUTPUT = """\
Company Holiday
    2026-04-21
    location: Everywhere
"""

# A date-like string buried in a URL (not a genuine date line) must not become
# the current date for a later time-only event.
_URL_DIGITS_OUTPUT = """\
Retro Notes
    notes: https://example.com/2025-01-01-retro-archive

Quick Sync
    14:00
"""

_USAGE_TEXT = """\
USAGE: icalBuddy  [options]  <command>

Where <command> is one of the following:

    eventsToday
    eventsNow
    eventsFrom:<start date> to:<end date>
    uncompletedTasks
    tasksDueBefore:<date>

See the icalBuddy man page for more info.
Version 1.10.1
Originally by Ali Rantakari, ali.rantakari.fi
"""


def _make_run_stub(stdout="", rc=0):
    def _run(args, **kw):
        return subprocess.CompletedProcess(
            args=args, returncode=rc, stdout=stdout, stderr=""
        )

    return _run


def test_gather_events_returns_empty_if_icalbuddy_missing(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which", lambda _: None
    )
    called = []
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
        lambda *a, **kw: called.append(a) or None,
    )

    result = gather_events(_SINCE, _UNTIL)

    assert result == []
    assert called == [], "subprocess.run should not be called when icalBuddy is missing"


def test_gather_events_parses_valid_output(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
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


def test_gather_events_parses_real_format_without_blank_lines(monkeypatch):
    """Regression: icalBuddy 1.10.x separates events by an unindented title
    line, not blank lines. All events must parse — not collapse into one."""
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
        _make_run_stub(stdout=_REAL_FORMAT_NO_BLANKS),
    )

    events = gather_events(_SINCE, _UNTIL)

    assert len(events) == 3
    titles = {e.title for e in events}
    assert titles == {"Standup", "Doctor Appointment", "Onsite Interview"}
    # The wrapped multi-line location stays attached to its event (one event,
    # not split by the indented continuation line).
    onsite = next(e for e in events if e.title == "Onsite Interview")
    assert onsite.location is not None and "Long Street" in onsite.location


def test_gather_events_skips_malformed_block(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
        _make_run_stub(stdout=_VALID_THEN_MALFORMED),
    )

    events = gather_events(_SINCE, _UNTIL)

    assert len(events) == 1
    assert events[0].title == "Team Sync"


def test_gather_events_parses_all_day_event(monkeypatch):
    """A bare-date line (no time) is an all-day event; start anchors at midnight."""
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
        _make_run_stub(stdout=_ALL_DAY_OUTPUT),
    )

    events = gather_events(_SINCE, _UNTIL)

    assert len(events) == 1
    assert events[0].title == "Company Holiday"
    assert events[0].start == datetime(2026, 4, 21, 0, 0, 0)
    assert events[0].location == "Everywhere"


def test_gather_events_url_digits_do_not_corrupt_date(monkeypatch):
    """Date-like digits inside a URL must not become the current date for a
    later time-only event (gap 6 date-scan anchor)."""
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
        _make_run_stub(stdout=_URL_DIGITS_OUTPUT),
    )

    events = gather_events(_SINCE, _UNTIL)

    # "Retro Notes" has no genuine date/time line, so it is skipped. "Quick Sync"
    # is time-only and must inherit the since-window date (2026-04-20), NOT the
    # 2025-01-01 buried in the URL above it.
    assert len(events) == 1
    assert events[0].title == "Quick Sync"
    assert events[0].start == datetime(2026, 4, 20, 14, 0, 0)


def test_gather_events_invocation_uses_joined_to_arg(monkeypatch):
    """Regression: `to:` and the end date must be a single arg, not separate args.

    Previously we emitted three args (`eventsFrom:DATE`, `to:`, `DATE`),
    which icalBuddy doesn't recognize → it printed usage text that we then
    fed into the event parser. The correct invocation is two args:
    `eventsFrom:DATE` and `to:DATE`.
    """
    captured: dict[str, list[str]] = {}

    def _capture(args, **kw):
        captured["args"] = args
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr("daily_driver.integrations.icalbuddy.subprocess.run", _capture)

    gather_events(_SINCE, _UNTIL)

    args = captured["args"]
    assert (
        "to:" not in args
    ), "bare `to:` arg with no date is the regression we're fixing"
    joined = [a for a in args if a.startswith("to:")]
    assert len(joined) == 1, f"expected exactly one `to:DATE` arg, got args={args!r}"
    assert joined[0] == "to:2026-04-22", f"unexpected to-arg shape: {joined[0]!r}"
    assert any(a.startswith("eventsFrom:") for a in args)


def test_gather_events_detects_usage_text_and_fails_loud(monkeypatch, caplog):
    """When icalBuddy emits its own usage/help text, fail loud rather than
    feeding the help text through the event-block parser."""
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
        _make_run_stub(stdout=_USAGE_TEXT),
    )

    result = gather_events(_SINCE, _UNTIL)

    assert result == []
    messages = [rec.getMessage() for rec in caplog.records]
    assert any(
        "usage" in m.lower() or "invocation" in m.lower() for m in messages
    ), f"expected a single clear error about icalBuddy invocation; got {messages!r}"
    block_warnings = [m for m in messages if "could not parse event block" in m]
    assert (
        block_warnings == []
    ), f"should not spam per-block parse warnings on usage text; got {block_warnings!r}"


def test_gather_events_nonzero_exit_returns_empty(monkeypatch, caplog):
    """Non-zero icalBuddy exit should be surfaced, not silently ignored."""
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
        _make_run_stub(stdout="", rc=1),
    )

    result = gather_events(_SINCE, _UNTIL)

    assert result == []
    assert any("icalbuddy" in rec.getMessage().lower() for rec in caplog.records)


def test_gather_events_zero_results(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.shutil.which",
        lambda _: "/usr/local/bin/icalBuddy",
    )
    monkeypatch.setattr(
        "daily_driver.integrations.icalbuddy.subprocess.run",
        _make_run_stub(stdout=""),
    )

    result = gather_events(_SINCE, _UNTIL)

    assert result == []
