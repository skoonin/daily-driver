"""Geography-only, country-first Location normalization (task #6).

The Location column holds geography only: a canonical full country name first,
then the city/region remainder in original order. Remote-ness moved to its own
Remote column, so remote tokens are stripped from Location here. A scrape source
that knows its per-search country passes an ``origin_country`` ISO code as a
fallback when the text itself names no country.
"""

from __future__ import annotations

import re

from daily_driver.plugins.job_search.scraper.countries import (
    canonical_country_name,
    detect_country,
)

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

# Separators a scraper may use between a remote marker and a place, beyond the
# comma split (e.g. "Remote - Germany", "Remote / US").
_NON_COMMA_SEP_RE = re.compile(r"\s*[/|]\s*|\s+-\s+")

_WS_RE = re.compile(r"\s+")


def _is_remote_token(token: str) -> bool:
    t = token.strip().lower()
    if not t:
        return False
    if t in _REMOTE_TOKENS:
        return True
    # "remote ok", "remote (us)", "remote - emea": any token that leads with the
    # bare word "remote" is a remote marker, not a place name.
    return t == "remote" or t.startswith("remote ") or t.startswith("remote-")


def _tokens(text: str) -> list[str]:
    """Split a location string into ordered tokens on commas and remote separators.

    Commas are the primary separator; the secondary separators ("/", "|", " - ")
    let "Remote - Germany" split into ["Remote", "Germany"] so the remote marker
    can be dropped independently of the place.
    """
    out: list[str] = []
    for comma_part in text.split(","):
        for piece in _NON_COMMA_SEP_RE.split(comma_part):
            cleaned = _WS_RE.sub(" ", piece).strip()
            if cleaned:
                out.append(cleaned)
    return out


def normalize_location(text: str, origin_country: str | None = None) -> str:
    """Return geography-only, country-first Location text.

    - Detects the country via the alias tables; the canonical full name leads.
    - Strips remote tokens (remote-ness is the Remote column's job).
    - Remainder keeps original order; dangling separators are cleaned.
    - No country in the text -> resolve from the ``origin_country`` ISO hint.
    - Genuinely nothing -> "" (never the literal "Remote").
    """
    raw = (text or "").strip()
    tokens = _tokens(raw)

    # Drop remote markers first so a remote-only string collapses to nothing.
    place_tokens = [tok for tok in tokens if not _is_remote_token(tok)]

    detected = detect_country(", ".join(place_tokens)) if place_tokens else None
    if detected is not None:
        alias, canonical = detected
        # Drop the token carrying the country alias; keep the rest in order.
        remainder = [tok for tok in place_tokens if not _token_is_alias(tok, alias)]
        parts = [canonical, *remainder]
    elif origin_country and (hint := canonical_country_name(origin_country)):
        # No country alias in the text: the scrape context's per-search country
        # wins. Drop a redundant bare ISO code ("US") the text may restate, which
        # the alias table excludes from detection but should not leak as remainder.
        code = origin_country.strip().lower()
        remainder = [tok for tok in place_tokens if tok.strip().lower() != code]
        parts = [hint, *remainder]
    else:
        # No country anywhere; pass the cleaned place tokens through verbatim.
        parts = place_tokens

    return ", ".join(p for p in parts if p)


def _token_is_alias(token: str, alias: str) -> bool:
    """Whether ``token`` is exactly the matched country alias (whole-word)."""
    return token.strip().lower() == alias.lower()


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
