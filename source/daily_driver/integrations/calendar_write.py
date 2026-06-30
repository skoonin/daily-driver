"""Write daily-plan time blocks to the local macOS Calendar via osascript.

The only place that writes events. Best-effort and injection-safe:

- Best-effort: mirrors ``notify.py``'s failure model (``check=False``, an
  explicit timeout, ``OSError`` / ``TimeoutExpired`` swallowed). A calendar
  failure logs a warning and returns a non-fatal result — it never raises into
  the user's session.
- Injection-safe: every user/event string (calendar name, tag, event title,
  notes) is passed as ``argv`` to an AppleScript that reads ``on run argv``;
  nothing is interpolated into the script body. This neutralizes quotes,
  backslashes, and AppleScript keywords (``tell`` / ``end tell``) in plan
  content. Do NOT copy ``notify.py``'s f-string interpolation here.
- Idempotent: each event's notes carry a ``daily-plan:<date>`` tag. Sync first
  deletes every event in the target calendar bearing that tag, then writes the
  current set — re-running is a clean replace, never a duplicate.
- macOS-only: guarded on ``sys.platform == "darwin"``; a clean no-op elsewhere.

Event times are seconds-of-day offsets added to an AppleScript midnight
``current date``, avoiding locale-driven date-string coercion.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date as date_cls

from daily_driver.core.logging import get_logger
from daily_driver.core.plan import CalendarEvent

log = get_logger(__name__)

# AppleScript reads its inputs from `argv` (`osascript - <args...>`), never from
# an interpolated body. Layout of argv: calendar name, tag, event count, then
# four items per event (title, start-seconds, end-seconds, notes).
_SCRIPT = """\
on run argv
    set calName to item 1 of argv
    set theTag to item 2 of argv
    set evCount to (item 3 of argv) as integer
    tell application "Calendar"
        set theCal to first calendar whose name is calName
        set toDelete to (every event of theCal whose description contains theTag)
        repeat with ev in toDelete
            delete ev
        end repeat
        set baseDate to current date
        set hours of baseDate to 0
        set minutes of baseDate to 0
        set seconds of baseDate to 0
        repeat with i from 1 to evCount
            set idx to 3 + ((i - 1) * 4)
            set evTitle to item (idx + 1) of argv
            set evStart to (item (idx + 2) of argv) as integer
            set evEnd to (item (idx + 3) of argv) as integer
            set evNotes to item (idx + 4) of argv
            set startDate to baseDate + evStart
            set endDate to baseDate + evEnd
            make new event at end of events of theCal with properties \
{summary:evTitle, start date:startDate, end date:endDate, description:evNotes}
        end repeat
    end tell
end run
"""


@dataclass(frozen=True)
class CalendarWriteResult:
    """Outcome of a sync. ``ok`` false carries a human-readable ``reason``."""

    ok: bool
    written: int
    reason: str | None = None


def available() -> bool:
    """True on macOS with `osascript` on PATH; warns with a hint otherwise."""
    if sys.platform != "darwin":
        log.debug("calendar: not macOS (%s); calendar write is a no-op", sys.platform)
        return False
    if shutil.which("osascript") is not None:
        return True
    log.warning(
        "calendar: osascript not found on PATH; skipping calendar write. "
        "See the calendar settings in docs/configuration.md."
    )
    return False


def write_day(
    calendar_name: str,
    day: date_cls,
    events: list[CalendarEvent],
) -> CalendarWriteResult:
    """Replace the day's plan events in ``calendar_name`` with ``events``.

    Deletes every event tagged ``daily-plan:<day>`` in the calendar, then
    writes ``events``. Never raises: a missing tool, non-darwin platform, or
    osascript failure returns a non-fatal ``CalendarWriteResult``.
    """
    if not available():
        return CalendarWriteResult(ok=False, written=0, reason="calendar unavailable")

    tag = f"daily-plan:{day.isoformat()}"
    argv: list[str] = [calendar_name, tag, str(len(events))]
    for event in events:
        argv += [
            event.title,
            str(event.start_seconds),
            str(event.end_seconds),
            event.notes,
        ]

    try:
        result = subprocess.run(
            ["osascript", "-", *argv],
            input=_SCRIPT,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("calendar: osascript failed (%s); plan not synced", exc)
        return CalendarWriteResult(ok=False, written=0, reason=str(exc))

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:200]
        log.warning(
            "calendar: osascript exited %d; stderr=%r. See the calendar settings "
            "in docs/configuration.md for permission steps.",
            result.returncode,
            stderr,
        )
        return CalendarWriteResult(
            ok=False, written=0, reason=stderr or "osascript error"
        )

    return CalendarWriteResult(ok=True, written=len(events))


__all__ = ["CalendarWriteResult", "write_day"]
