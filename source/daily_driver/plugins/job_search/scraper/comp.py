"""Compensation display helpers: format a found amount for the Comp column."""

from __future__ import annotations

from typing import Any

# Currency code → display prefix. Anything not listed falls through to the
# raw code prefixed with a space (e.g. "EUR 100,000/yr").
_COMP_CURRENCY_PREFIX = {
    "USD": "$",
    "CAD": "CA$",
    "GBP": "£",
    "EUR": "€",
}

# Schema.org unitText → suffix. JobPosting commonly uses YEAR/MONTH/HOUR.
_COMP_UNIT_SUFFIX = {
    "YEAR": "/yr",
    "MONTH": "/mo",
    "WEEK": "/wk",
    "DAY": "/day",
    "HOUR": "/hr",
}


def _to_int(x: object) -> int | None:
    """Best-effort coerce an arbitrary value to int (via float). None on failure."""
    try:
        return int(float(x))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _format_comp(base_salary: dict[str, Any]) -> str:
    """Render a JSON-LD MonetaryAmount into a short human string.

    Returns "" for any shape that doesn't yield at least one numeric value —
    callers treat empty as "no comp data" and leave the CSV column blank.
    """
    if not isinstance(base_salary, dict):
        return ""
    currency = (base_salary.get("currency") or "").strip().upper()
    value = base_salary.get("value")

    # value can be a QuantitativeValue dict or a bare number/string
    if isinstance(value, dict):
        min_v = value.get("minValue")
        max_v = value.get("maxValue")
        single = value.get("value")
        unit = (value.get("unitText") or "").strip().upper()
    else:
        min_v = max_v = None
        single = value
        unit = (base_salary.get("unitText") or "").strip().upper()

    lo, hi, mid = _to_int(min_v), _to_int(max_v), _to_int(single)
    if lo is not None and hi is not None and lo != hi:
        amount = f"{lo:,}–{hi:,}"
    elif mid is not None:
        amount = f"{mid:,}"
    elif lo is not None:
        amount = f"{lo:,}"
    else:
        return ""

    prefix = _COMP_CURRENCY_PREFIX.get(currency)
    if prefix is None:
        # Unknown currency: keep the code so the user can see what it is.
        prefix = f"{currency} " if currency else ""
    suffix = _COMP_UNIT_SUFFIX.get(unit, "")
    return f"{prefix}{amount}{suffix}"


__all__ = [
    "_COMP_CURRENCY_PREFIX",
    "_COMP_UNIT_SUFFIX",
    "_format_comp",
    "_to_int",
]
