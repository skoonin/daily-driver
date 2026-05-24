"""Compensation parsing and threshold/currency filters."""

from __future__ import annotations

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


def _format_comp(base_salary: dict) -> str:
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


def _parse_comp(comp_str: str) -> dict:
    """Legacy dict-shape wrapper around ``Comp.parse``.

    Kept for the dict-based pipeline and ``comp_meets_threshold``. Returns
    ``comp_min_native``, ``comp_max_native``, ``comp_currency``, ``comp_period``,
    ``comp_min_usd``, ``comp_max_usd``. All ``None`` / ``""`` on unparseable input.
    """
    from daily_driver.scraper.models import Comp

    c = Comp.parse(comp_str)
    if not c.is_known:
        return {
            "comp_min_native": None,
            "comp_max_native": None,
            "comp_currency": "",
            "comp_period": "",
            "comp_min_usd": None,
            "comp_max_usd": None,
        }
    return {
        "comp_min_native": c.min_native,
        "comp_max_native": c.max_native,
        "comp_currency": c.currency or "",
        "comp_period": c.period,
        "comp_min_usd": c.min_usd,
        "comp_max_usd": c.max_usd,
    }


def comp_meets_threshold(job: dict, config: dict) -> tuple[bool, str]:
    """Return (True, "") if comp is acceptable or unknown; (False, reason) if below threshold.

    Fails open when comp_max_usd is absent or zero so roles with no listed
    comp reach CSV for manual review rather than being silently dropped.
    """
    from daily_driver.scraper.runner import min_comp_usd

    threshold = min_comp_usd(config)
    cmax = job.get("comp_max_usd")
    if not cmax:
        return (True, "")
    if cmax >= threshold:
        return (True, "")
    return (False, f"below comp threshold (max ${cmax:,} < ${threshold:,})")


def currency_matches_primary(job: dict, config: dict) -> bool:
    """Return True if ``job`` should pass the primary-currency filter (#48).

    When ``plugins.job_search.primary_currency`` is unset, every job passes.
    When set, jobs with a parsed ``comp_currency`` matching pass; jobs with an
    empty/missing currency (unparseable comp) also pass — currency=None is the
    sentinel for "couldn't read", not "doesn't match".
    """
    from daily_driver.scraper.runner import _model

    primary = _model(config).primary_currency
    if primary is None:
        return True
    job_currency = job.get("comp_currency") or ""
    if not job_currency:
        return True
    return job_currency == primary


__all__ = [
    "_COMP_CURRENCY_PREFIX",
    "_COMP_UNIT_SUFFIX",
    "_format_comp",
    "_parse_comp",
    "comp_meets_threshold",
    "currency_matches_primary",
    "_to_int",
]
