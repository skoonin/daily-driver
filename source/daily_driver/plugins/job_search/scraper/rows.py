"""Row-level helpers: location matching, dedup keys, and the raw-dict lift.

The pure functions the sink and the orchestrator share to dedup, filter, and
lift scraped dicts into typed rows. No I/O, no logging. Depends on
:mod:`models` at import time; the reverse edge stays LAZY on the models side
(``NormalizedJob.from_raw`` imports the remote-alias constants in-function),
so hoisting that import would reintroduce the cycle.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.countries import (
    country_named_in,
    detect_country,
)
from daily_driver.plugins.job_search.scraper.models import (
    BOARD_SOURCE_CANONICALS,
    split_source,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob


def _word_in_location(phrase: str, loc_lower: str) -> bool:
    """Whole-word match of a lowercased phrase against a location string.

    Word boundaries keep "Vancouver" from hitting "Vancouverish" and the 2-char
    country alias "uk" from hitting "Tukwila"; a bare substring test is too loose
    once city and country names drive acceptance.
    """
    pattern = rf"(?<![a-z0-9]){re.escape(phrase.strip().lower())}(?![a-z0-9])"
    return re.search(pattern, loc_lower) is not None


def _country_named(loc_lower: str, code: str) -> bool:
    """Whether a lowercased location names the country `code` (whole-word, any alias).

    Delegates to :func:`country_named_in`, which also handles sub-national
    shadow phrases ("New South Wales" must not hit a GB "wales" check).
    """
    return country_named_in(loc_lower, code)


def location_matches(job: dict[str, Any], plugin: JobSearchPlugin) -> bool:
    """Check whether a job's location matches the configured allow-map.

    Accepts if any of:
      - remote: true and job location contains "remote" (or is empty/missing).
        By default remote is scoped to `countries`: a remote role passes if its
        location names any configured country or no country at all, and is
        dropped only when it names some other country. An empty `countries` map
        imposes no restriction. Set `remote_unlisted_countries` to accept remote
        roles from any country (country-blind).
      - job location contains a country name from locations.countries whose
        city list is empty (whole-country acceptance)
      - job location names a listed city of a mapped country. The city match
        stands alone — real ATS location strings usually name the city
        WITHOUT the country ("Movable Ink - Toronto", "New York, New York"),
        so requiring both would drop nearly everything. Conversely, for a
        city-narrowed country the bare country name does NOT pass: naming
        cities means "only these cities (or remote)".
    Returns True (accept) when no locations block is configured.
    """
    loc_cfg = plugin.locations
    if loc_cfg is None:
        return True

    loc = job.get("location", "").lower()
    if not loc:
        # Empty location implies remote-friendly; accept when remote is enabled
        return loc_cfg.remote

    if loc_cfg.remote and "remote" in loc:
        if loc_cfg.remote_unlisted_countries or not loc_cfg.countries:
            return True
        # Remote keys off the configured country set (city narrowing is
        # onsite-only): accept if the location names any configured country;
        # drop only when it names some other country. A location naming no
        # country at all (bare "Remote") is ambiguous and accepted.
        if any(_country_named(loc, code) for code in loc_cfg.countries):
            return True
        return detect_country(loc) is None

    for code, cities in loc_cfg.countries.items():
        if cities:
            if any(_word_in_location(city, loc) for city in cities):
                return True
        elif _country_named(loc, code):
            return True

    return False


# ── Dedup helpers ─────────────────────────────────────────────────────────────


_WHITESPACE_RE = re.compile(r"\s+")


def _dedup_norm(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", s.lower().strip())


def dedup_key(company: str, role: str) -> str:
    """Normalized dedup key for cross-site duplicate detection.

    Lowercases and collapses whitespace in both fields so the same job posted
    on RemoteOK and LinkedIn produces an identical key.
    """
    return f"{_dedup_norm(company)}::{_dedup_norm(role)}"


def _csv_row_identity(row: dict[str, str]) -> str:
    """Stable identity for a jobs.csv row: stripped Link URL, else dedup key.

    Mirrors the append-time dedup boundary (URL primary, ``(company, role)``
    fallback) so a row's identity is the same whether it is held in memory as an
    ``EnrichedJob`` or read back as a raw CSV dict. Used by ``_JobSink.flush`` to
    merge this run's rows against the current on-disk rows by identity.
    """
    url = (row.get("Link") or "").strip()
    if url:
        return url
    # csv.DictReader fills missing trailing cells with None for a row shorter
    # than the header (a hand-edit slip), so coerce to str -- dedup_key lower()s
    # its inputs and would otherwise raise AttributeError on None mid-flush.
    return dedup_key(row.get("Company") or "", row.get("Role") or "")


def _is_board_source(source: str) -> bool:
    """True when a Source string names a full-enumeration board (ATS) source."""
    return split_source((source or "").strip())[0] in BOARD_SOURCE_CANONICALS


def _board_upgrade_target(
    current_source: str, job: dict[str, Any]
) -> tuple[str, str] | None:
    """``(new_source, new_url)`` when ``job`` is a board record that should
    replace an aggregator row's Source/Link, else None.

    The one eligibility rule for both upgrade paths (carried CSV-dict rows and
    this-run EnrichedJob rows); the untriaged-status gate stays with the
    callers, which hold the row in different shapes.
    """
    new_source = job.get("source") or ""
    new_url = (job.get("url") or "").strip()
    if not new_url or not _is_board_source(new_source):
        return None
    if _is_board_source(current_source):
        return None
    return new_source, new_url


def _better_sighting(prev: dict[str, Any] | None, job: dict[str, Any]) -> bool:
    """Should ``job`` replace ``prev`` in the re-sighting collector?

    A board-source sighting always beats an aggregator one (it is the record
    the source-upgrade preference acts on); within the same tier, prefer a
    sighting that carries a description -- the same job may re-appear from a
    unit that lacks the body, and the heal must not lose the fuller record.
    """
    if prev is None:
        return True
    prev_board = _is_board_source(prev.get("source", ""))
    job_board = _is_board_source(job.get("source", ""))
    if job_board != prev_board:
        return job_board
    has_desc = bool((job.get("description_text") or "").strip())
    prev_has_desc = bool((prev.get("description_text") or "").strip())
    return has_desc and not prev_has_desc


# Location strings scrapers emit for fully-remote roles. All collapse to "Remote"
# so downstream consumers see one canonical value.
_REMOTE_LOCATION_ALIASES: frozenset[str] = frozenset(
    {
        "",
        "anywhere",
        "worldwide",
        "remote - anywhere",
        "remote, worldwide",
    }
)

# Role-title suffixes appended by some scrapers to signal remote eligibility.
# Stripped here so dedup and display see clean titles.
_REMOTE_ROLE_SUFFIXES: tuple[str, ...] = (" (remote)", "(remote)", " - remote")


def _enriched_from_scraped(job: dict[str, Any]) -> EnrichedJob:  # noqa: F821
    """Lift one merged source dict into a fully-typed ``EnrichedJob``.

    The source-adapter boundary stays dict-based: scrapers emit raw dicts
    (``comp`` display string, ISO ``date_found``, optional ``description_text``).
    This validates that dict through ``RawScrapedJob`` — the one place the
    pipeline crosses from untyped wire data into the typed models — then runs
    the standard ``from_raw`` -> ``from_normalized`` transitions. ``description_text``
    is threaded separately because ``RawScrapedJob`` does not model enrichment
    fields.
    """
    import datetime as dt

    from daily_driver.plugins.job_search.scraper.models import (
        EnrichedJob,
        NormalizedJob,
        RawScrapedJob,
    )
    from daily_driver.plugins.job_search.scraper.normalize_location import (
        detect_remote,
        normalize_location,
    )

    date_found = job.get("date_found")
    if isinstance(date_found, str) and date_found:
        try:
            date_found_val: dt.date = dt.date.fromisoformat(date_found)
        except ValueError:
            date_found_val = dt.date.today()  # noqa: DTZ011
    elif isinstance(date_found, dt.date):
        date_found_val = date_found
    else:
        date_found_val = dt.date.today()  # noqa: DTZ011

    raw = RawScrapedJob.model_validate(
        {
            "company": job.get("company", ""),
            # Coerce on STRIPPED content: a whitespace-only role is truthy, so a
            # bare `or` fallback would keep it, and RawScrapedJob then strips it to
            # '' and the NonEmptyStr validator raises -- aborting the run.
            "role": (job.get("role") or "").strip() or "(unknown)",
            "url": job.get("url", ""),
            "source": job.get("source", "") or "unknown",
            "location": job.get("location", ""),
            "comp_display": job.get("comp", "") or "",
            "date_found": date_found_val,
        }
    )
    enriched = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))

    # Location becomes geography-only, country-first; remote-ness moves to the
    # Remote column. Both read the RAW scraped location/role (the location filter
    # already ran on that raw text upstream), with the per-search origin-country
    # ISO code as the country fallback when the text names none.
    raw_location = job.get("location", "") or ""
    origin_country = job.get("origin_country") or None
    location = normalize_location(raw_location, origin_country)
    remote = detect_remote(raw_location, job.get("role", "") or "")

    updates: dict[str, Any] = {"location": location, "remote": remote}
    desc = job.get("description_text", "")
    if desc:
        updates["description_text"] = desc
    return enriched.model_copy(update=updates)
