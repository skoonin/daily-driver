"""Integration tests for the typed K2-K3 pipeline path:

    JobSpy row dict -> RawScrapedJob -> NormalizedJob

Verifies the end-to-end typed flow stays cohesive as later iterations migrate
the dict-based orchestrator over.
"""

from __future__ import annotations

import datetime as dt

from daily_driver.plugins.job_search.scraper.models import NormalizedJob, RawScrapedJob
from daily_driver.plugins.job_search.scraper.sources.jobspy import jobspy_row_to_raw


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
    norm = NormalizedJob.from_raw(raw)
    assert isinstance(norm, NormalizedJob)
    assert norm.company == "Acme"
    assert norm.role == "SRE Engineer"  # remote suffix stripped
    assert norm.location == "Remote"  # alias collapsed
    assert norm.source == "linkedin"
    assert norm.source_canonical == "linkedin"
    assert norm.source_board == ""
    assert norm.comp == "$150,000–200,000/yr"
    assert norm.date_found == dt.date.today()  # noqa: DTZ011


def test_greenhouse_source_split() -> None:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="Greenhouse (acme-corp)",
    )
    norm = NormalizedJob.from_raw(raw)
    assert norm.source_canonical == "greenhouse"
    assert norm.source_board == "acme-corp"
    # source preserved verbatim for CSV.
    assert norm.source == "Greenhouse (acme-corp)"


def test_ashby_source_split() -> None:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="Ashby (acme-corp)",
    )
    norm = NormalizedJob.from_raw(raw)
    # Mirrors greenhouse: the "Ashby (<board>)" string unifies under a single
    # canonical source so multi-board Ashby rows don't fragment when grouped.
    assert norm.source_canonical == "ashby"
    assert norm.source_board == "acme-corp"
    assert norm.source == "Ashby (acme-corp)"


def test_lever_source_split() -> None:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="Lever (acme-corp)",
    )
    norm = NormalizedJob.from_raw(raw)
    # Mirrors greenhouse/ashby: "Lever (<board>)" unifies under one canonical
    # source so multi-board Lever rows don't fragment when grouped.
    assert norm.source_canonical == "lever"
    assert norm.source_board == "acme-corp"
    assert norm.source == "Lever (acme-corp)"


def test_workable_source_split() -> None:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="Workable (acme-corp)",
    )
    norm = NormalizedJob.from_raw(raw)
    # Mirrors greenhouse/ashby: "Workable (<slug>)" unifies under one canonical
    # source so multi-account Workable rows don't fragment when grouped.
    assert norm.source_canonical == "workable"
    assert norm.source_board == "acme-corp"
    assert norm.source == "Workable (acme-corp)"


def test_workday_source_split() -> None:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="Workday (acme-corp)",
    )
    norm = NormalizedJob.from_raw(raw)
    # Mirrors the other ATS sources: "Workday (<tenant>)" unifies under one
    # canonical source so multi-board Workday rows don't fragment when grouped.
    assert norm.source_canonical == "workday"
    assert norm.source_board == "acme-corp"
    assert norm.source == "Workday (acme-corp)"


def test_from_raw_is_pure() -> None:
    raw = RawScrapedJob(company="A", role="R", url="u", source="s", location="LOC")
    norm1 = NormalizedJob.from_raw(raw)
    norm2 = NormalizedJob.from_raw(raw)
    assert norm1 == norm2
