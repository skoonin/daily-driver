"""Integration tests for the typed K2-K3 pipeline path:

    JobSpy row dict -> RawScrapedJob -> NormalizedJob

Verifies the end-to-end typed flow stays cohesive as later iterations migrate
the dict-based orchestrator over.
"""

from __future__ import annotations

import datetime as dt

from daily_driver.scraper.models import NormalizedJob, RawScrapedJob
from daily_driver.scraper.runner import normalize_typed
from daily_driver.scraper.sources.jobspy import jobspy_row_to_raw


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "company": "Acme",
        "title": "SRE Engineer (Remote)",
        "location": "Anywhere",
        "job_url": "https://example.com/job/1",
        "site": "linkedin",
        "description": "...",
        "min_amount": 150_000,
        "max_amount": 200_000,
        "currency": "USD",
        "interval": "yearly",
    }
    base.update(overrides)
    return base


def test_jobspy_to_normalized_end_to_end() -> None:
    raw = jobspy_row_to_raw(_row())
    assert raw is not None
    norm = normalize_typed(raw)
    assert isinstance(norm, NormalizedJob)
    assert norm.company == "Acme"
    assert norm.role == "SRE Engineer"  # remote suffix stripped
    assert norm.location == "Remote"  # alias collapsed
    assert norm.source == "linkedin"
    assert norm.source_canonical == "linkedin"
    assert norm.source_board == ""
    assert norm.comp.is_known and norm.comp.currency == "USD"
    assert norm.date_found == dt.date.today()  # noqa: DTZ011


def test_greenhouse_source_split() -> None:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="Greenhouse (acme-corp)",
    )
    norm = normalize_typed(raw)
    assert norm.source_canonical == "greenhouse"
    assert norm.source_board == "acme-corp"
    # source preserved verbatim for CSV.
    assert norm.source == "Greenhouse (acme-corp)"


def test_normalize_typed_is_pure() -> None:
    raw = RawScrapedJob(company="A", role="R", url="u", source="s", location="LOC")
    norm1 = normalize_typed(raw)
    norm2 = normalize_typed(raw)
    assert norm1 == norm2


def test_dedup_typed_matches_legacy() -> None:
    """K5: dedup_key_for(NormalizedJob) == dedup_key(company, role)."""
    from daily_driver.scraper.runner import dedup_key, dedup_key_for

    raw = RawScrapedJob(
        company="  Acme  Corp ",
        role="Senior  SRE",
        url="u",
        source="remoteok",
    )
    norm = normalize_typed(raw)
    assert dedup_key_for(norm) == dedup_key("  Acme  Corp ", "Senior  SRE")
    assert dedup_key_for(norm) == "acme corp::senior sre"


def test_dedup_typed_collapses_whitespace_and_case() -> None:
    from daily_driver.scraper.runner import dedup_key_for

    a = normalize_typed(RawScrapedJob(company="ACME", role="SRE", url="u1", source="s"))
    b = normalize_typed(
        RawScrapedJob(company="acme", role="  sre  ", url="u2", source="s")
    )
    assert dedup_key_for(a) == dedup_key_for(b)
