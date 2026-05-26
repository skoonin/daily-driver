from __future__ import annotations

from datetime import date


def resolve_date(raw: str | None) -> date:
    """Return today() if raw is None, else date.fromisoformat(raw)."""
    if raw is None:
        return date.today()
    return date.fromisoformat(raw)
