"""JobSpy-enum-derived country lookup tables + Apple postLocation resolver."""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import quote

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

# Curated location-text aliases JobSpy's country names omit: the constituent
# UK nations plus "UK". The >2-char filter below applies only to enum-derived
# names (so bare ISO codes like "us" can't false-match "Austin"); aliases here
# are added verbatim, deliberately keeping the safe 2-char "uk".
_COUNTRY_NAME_ALIASES: dict[str, list[str]] = {
    "GB": ["uk", "england", "scotland", "wales"],
}


@functools.lru_cache(maxsize=1)
def _country_table() -> dict[str, tuple[list[str], str]]:
    """ISO alpha-2 -> (location-match aliases, JobSpy country name).

    Derived from JobSpy's ``Country`` enum so every country JobSpy can scrape is
    supported, and the location filter never silently desyncs from a hand-kept
    list. Lazy + cached: the heavy jobspy import is deferred until country
    matching is actually needed, keeping cheap CLI commands fast.
    """
    from jobspy.model import Country

    table: dict[str, tuple[list[str], str]] = {}
    for c in Country:
        raw = c.value[1] if len(c.value) > 1 else ""
        # value[1] is the ISO code, sometimes "subdomain:iso" (e.g. "www:us",
        # "uk:gb"); take the part after the colon when present.
        code = (raw.partition(":")[2] or raw).upper()
        if len(code) != 2:  # skip meta-entries like WORLDWIDE ("www")
            continue
        raw_names = [n.strip() for n in c.value[0].split(",") if n.strip()]
        aliases = [n for n in raw_names if len(n) > 2] + _COUNTRY_NAME_ALIASES.get(
            code, []
        )
        table[code] = (aliases, raw_names[0])
    return table


def country_names(code: str) -> list[str]:
    """Location-text aliases for an ISO alpha-2 code ([] if JobSpy lacks it)."""
    entry = _country_table().get(code.upper())
    return entry[0] if entry else []


def jobspy_country(code: str, default: str) -> str:
    """JobSpy country name for an ISO code, or ``default`` if JobSpy lacks it."""
    entry = _country_table().get(code.upper())
    return entry[1] if entry else default


# jobs.apple.com locale is display-language only, not the job's country. en-us
# yields English job titles for every country so role matching works everywhere;
# country scoping is the separate postLocation filter, not the locale path.
APPLE_LOCALE = "en-us"


def apple_postlocation_code(
    names: Iterable[str], fetch_json: Callable[[str], Any]
) -> str | None:
    """Resolve Apple's internal postLocation code for a country.

    Apple's postLocation codes are Apple-internal (Germany=DEU, Canada=CANC,
    Spain=ESPC) — not ISO3 and not rule-derivable — so the only authoritative
    source is Apple's refData API. ``fetch_json`` is an injected callable that
    GETs the URL and returns the parsed JSON dict, keeping this transport-
    agnostic (no requests/playwright import) and unit-testable without network.

    ``names`` is the country's candidate names (e.g. ``country_names("US")`` ->
    ``["usa", "united states"]``), tried in order: JobSpy's primary name is often
    an abbreviation Apple's API does not recognise ("usa" returns nothing, "uk"
    returns "Ukraine"), while the spelled-out name resolves. For each candidate,
    filters results to ``level == 1`` and matches the entry whose ``name`` equals
    that candidate case-insensitively and exactly — the exact match disambiguates
    "United States" from "United States Minor Outlying Islands" and rejects
    near-misses like "uk" -> "Ukraine". Returns the first matching ``code``, or
    None if no candidate resolves.
    """
    for name in names:
        url = f"https://jobs.apple.com/api/v1/refData/postlocation?input={quote(name)}"
        data = fetch_json(url)
        for entry in data.get("res", []) or []:
            if (
                entry.get("level") == 1
                and entry.get("name", "").casefold() == name.casefold()
            ):
                code: str | None = entry.get("code")
                if code:
                    return code
    return None
