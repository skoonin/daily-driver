"""Durations written as text, such as `30m`, `24h`, `1d`, or `1h30m`.

A leaf module with no daily_driver imports, so config models and CLI code can
both use it.
"""

from __future__ import annotations

import re
from datetime import timedelta

_DURATION = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?")


def parse_duration(text: str) -> timedelta:
    """The duration `text` names: days, hours, then minutes, each optional.

    Raises ValueError for text with no unit, parts out of order, or a
    duration too long to represent.
    """
    match = _DURATION.fullmatch(text.strip().lower())
    if not match or not any(match.groups()):
        raise ValueError(f"invalid duration {text!r}: use 30m, 24h, 1d, or 1h30m")
    days, hours, minutes = (int(part or 0) for part in match.groups())
    try:
        return timedelta(days=days, hours=hours, minutes=minutes)
    except OverflowError:
        raise ValueError(f"invalid duration {text!r}: too long") from None
