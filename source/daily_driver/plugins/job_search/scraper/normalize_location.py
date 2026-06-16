"""Geography-only, country-first Location normalization (task #6).

The Location column holds geography only: a canonical full country name first,
then the city/region remainder in original order. Remote-ness moved to its own
Remote column, so remote tokens are stripped from Location here. A scrape source
that knows its per-search country passes an ``origin_country`` ISO code as a
fallback when the text itself names no country.

When a string names more than one country (e.g. "US or Canada"), the first one
in text order becomes the primary; the connective and the later country are
dropped (no duplication, no fake city).
"""

from __future__ import annotations

import re

from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.countries import (
    canonical_country_name,
    detect_country,
)

log = get_logger(__name__)

# Remote-signalling tokens stripped from Location text. Matched whole-token
# (case-insensitive) so a city like "Remoteville" is never mangled. "remote ok",
# "remote-friendly", etc. collapse via the leading-"remote" check below.
_REMOTE_TOKENS: frozenset[str] = frozenset(
    {
        "remote",
        "remote ok",
        "remote-ok",
        "remote friendly",
        "remote-friendly",
        "anywhere",
        "worldwide",
        "fully remote",
        "100% remote",
    }
)

# Token separators beyond the comma split: "/", "|", " - " (dash with spaces),
# the connective " or " / " and ", and parentheses. Splitting on " or " lets
# "US or Canada" become ["US", "Canada"] so the loser country + connective drop
# out; unwrapping parens lets "Remote (US)" become ["Remote", "US"].
_SEP_RE = re.compile(
    r"\s*[/|()]\s*|\s+-\s+|\s+\bor\b\s+|\s+\band\b\s+",
    re.IGNORECASE,
)

_WS_RE = re.compile(r"\s+")


def _is_remote_token(token: str) -> bool:
    t = token.strip().lower()
    if not t:
        return False
    if t in _REMOTE_TOKENS:
        return True
    # "remote ok", "remote (us)", "remote - emea": any token that leads with the
    # bare word "remote" is a remote marker, not a place name. Its remainder is
    # re-examined for geography by the caller before the token is dropped.
    return t == "remote" or t.startswith("remote ") or t.startswith("remote-")


def _tokens(text: str) -> list[str]:
    """Split a location string into ordered tokens on commas and the separators.

    Commas are the primary separator; the secondary separators (see ``_SEP_RE``)
    handle remote markers, connectives, and parenthesised codes so each place /
    country / marker becomes its own token.
    """
    out: list[str] = []
    for comma_part in text.split(","):
        for piece in _SEP_RE.split(comma_part):
            cleaned = _WS_RE.sub(" ", piece).strip()
            if cleaned:
                out.append(cleaned)
    return out


def _bare_iso_country(token: str) -> str | None:
    """Canonical name when ``token`` is exactly a 2-letter ISO code, else None.

    The alias tables deliberately exclude bare 2-char codes (so "us" can't match
    "Austin"), but a standalone token like "US" in "Remote (US)" is an
    unambiguous country reference, so resolve it here.
    """
    t = token.strip()
    if len(t) == 2 and t.isalpha():
        return canonical_country_name(t)
    return None


def _country_in_token(token: str) -> tuple[str, str] | None:
    """Country named in a single token -> (canonical_name, leftover_remainder).

    Detects a country either as a whole-word alias inside the token (the alias is
    removed by whole-word substitution, leaving any other words as remainder) or
    as a bare 2-letter ISO code (whole token, no remainder). Returns None when the
    token names no country.
    """
    detected = detect_country(token)
    if detected is not None:
        alias, canonical = detected
        pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
        leftover = _WS_RE.sub(" ", re.sub(pattern, " ", token, flags=re.IGNORECASE))
        return canonical, leftover.strip()
    bare = _bare_iso_country(token)
    if bare is not None:
        return bare, ""
    return None


def normalize_location(text: str, origin_country: str | None = None) -> str:
    """Return geography-only, country-first Location text.

    - Detects the country via the alias tables; the canonical full name leads.
    - On multiple countries, the first in text order wins; connective + loser drop.
    - Strips remote tokens (remote-ness is the Remote column's job).
    - Remainder keeps original order; dangling separators are cleaned.
    - No country in the text -> resolve from the ``origin_country`` ISO hint.
    - Genuinely nothing -> "" (never the literal "Remote").
    """
    raw = (text or "").strip()
    tokens = _tokens(raw)

    # Drop remote markers, but re-examine each marker's remainder for geography
    # so "Remote (US)" / "Remote - Germany" don't swallow the country.
    place_tokens: list[str] = []
    for tok in tokens:
        if _is_remote_token(tok):
            continue
        place_tokens.append(tok)

    primary: str | None = None
    remainder: list[str] = []
    countries_seen = 0
    for tok in place_tokens:
        found = _country_in_token(tok)
        if found is None:
            remainder.append(tok)
            continue
        countries_seen += 1
        canonical, leftover = found
        if primary is None:
            primary = canonical
            if leftover:
                remainder.append(leftover)
        # A second (or later) country is the loser: drop it and its leftover.

    if primary is not None:
        parts = [primary, *remainder]
    elif origin_country and (hint := canonical_country_name(origin_country)):
        # No country in the text: the scrape context's per-search country wins.
        # Drop a redundant bare ISO code the text may restate.
        code = origin_country.strip().lower()
        remainder = [tok for tok in remainder if tok.strip().lower() != code]
        parts = [hint, *remainder]
    else:
        parts = remainder

    result = ", ".join(p for p in parts if p)

    # The lossy branches become observable: a non-empty input that collapses to
    # nothing (all remote/connective), or a multi-country string where only the
    # first was kept.
    if raw and not result:
        log.debug("normalize_location: %r -> empty (no geography after strip)", raw)
    if countries_seen > 1:
        log.debug(
            "normalize_location: %r named %d countries; kept first (%s)",
            raw,
            countries_seen,
            primary,
        )
    return result


def detect_remote(location: str, role: str) -> str:
    """Free heuristic tier: "remote" when raw location/title carries a remote token.

    Covers the ``--no-enrich`` path with zero cost. Returns "remote" or "" only;
    the finer "hybrid"/"onsite" distinction is left to the LLM tier.
    """
    haystack = f"{location} {role}".lower()
    if re.search(r"(?<![a-z])remote(?![a-z])", haystack):
        return "remote"
    for token in ("anywhere", "worldwide"):
        if re.search(rf"(?<![a-z]){token}(?![a-z])", haystack):
            return "remote"
    return ""
