"""Integration tests for the typed K2-K3 pipeline path:

    JobSpy row dict -> RawScrapedJob -> NormalizedJob

Verifies the end-to-end typed flow stays cohesive as later iterations migrate
the dict-based orchestrator over.
"""

from __future__ import annotations

import datetime as dt

from daily_driver.scraper._impl import jobspy_row_to_raw, normalize_typed
from daily_driver.scraper.models import NormalizedJob, RawScrapedJob


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
