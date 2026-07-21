"""Subprocess wrapper for the macOS `icalBuddy` CLI.

The only place in the codebase that shells out to `icalBuddy`. The calendar
gather receives raw stdout (or ``None`` when there is nothing to parse) and
owns all parsing; it never imports `subprocess`.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime

from daily_driver.core.logging import get_logger

log = get_logger(__name__)


def available() -> bool:
    """True if `icalBuddy` is on PATH; warns with install hint when missing."""
    if shutil.which("icalBuddy") is not None:
        return True
    log.warning(
        "calendar: icalBuddy not found on PATH; skipping calendar gather. "
        "Install via `brew install ical-buddy` and grant the terminal "
        "Calendar access (System Settings -> Privacy & Security -> "
        "Calendars). See docs/developer.md 'Calendar (icalBuddy) setup'."
    )
    return False


def events_between(since: datetime, until: datetime) -> str | None:
    """Run `icalBuddy eventsFrom:..to:..` and return its raw stdout.

    Returns ``None`` (the gather then yields no events) when:
      - `icalBuddy` is not on PATH,
      - the command times out (30s bound),
      - `icalBuddy` exits non-zero (logged with stderr + setup hint).

    icalBuddy output is not machine-stable; parsing is the caller's job.
    """
    if not available():
        return None

    cmd = [
        "icalBuddy",
        "-iep",
        "title,datetime,location",
        "-nc",
        # No relative dates: today/tomorrow/yesterday events must print an ISO
        # date line the parser can read, not "today at 09:30" (which matches no
        # regex and gets silently dropped).
        "-nrd",
        "-b",
        "",
        "-df",
        "%Y-%m-%d",
        "-tf",
        "%H:%M",
        f"eventsFrom:{since.strftime('%Y-%m-%d')}",
        f"to:{until.strftime('%Y-%m-%d')}",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=30
        )
    except subprocess.TimeoutExpired:
        log.warning("calendar: icalbuddy timed out after 30s; returning no events")
        return None

    if result.returncode != 0:
        log.warning(
            "calendar: icalBuddy exited %d; stderr=%r. See docs/developer.md "
            "'Calendar (icalBuddy) setup' for plist + permission steps.",
            result.returncode,
            (result.stderr or "").strip()[:200],
        )
        return None

    return result.stdout
