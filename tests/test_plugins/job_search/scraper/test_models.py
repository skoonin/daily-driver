"""Isolated tests for scraper.models — no callers wired up yet (K1)."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    NormalizedJob,
    RawScrapedJob,
    Source,
    parse_fit,
)

# --------------------------------------------------------------------------- #
# RawScrapedJob — Q15: extra="ignore"
# --------------------------------------------------------------------------- #


class TestRawScrapedJob:
    def test_minimal(self) -> None:
        r = RawScrapedJob(
            company="Acme",
            role="SRE",
            url="https://example.com/job",
            source="remoteok",
        )
        assert r.location == "" and r.comp_display == ""
        assert r.date_found == dt.date.today()  # noqa: DTZ011

    def test_extra_ignored_q15(self) -> None:
        # jobspy schema drift: unknown keys must NOT raise.
        r = RawScrapedJob(
            company="Acme",
            role="SRE",
            url="https://example.com/job",
            source="jobspy",
            mystery_field="from_some_future_jobspy_release",  # type: ignore[call-arg]
        )
        assert r.company == "Acme"
        assert not hasattr(r, "mystery_field")

    def test_url_stripped(self) -> None:
        r = RawScrapedJob(company="A", role="R", url="  https://x  ", source="s")
        assert r.url == "https://x"

    def test_role_must_be_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            RawScrapedJob(company="A", role="   ", url="u", source="s")

    def test_frozen(self) -> None:
        r = RawScrapedJob(company="A", role="R", url="u", source="s")
        with pytest.raises(ValidationError):
            r.company = "B"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# NormalizedJob.from_raw
# --------------------------------------------------------------------------- #


class TestNormalizedJob:
    def test_remote_alias_collapses(self) -> None:
        raw = RawScrapedJob(
            company="A",
            role="SRE",
            url="u",
            source="remoteok",
            location="Anywhere",
        )
        n = NormalizedJob.from_raw(raw)
        assert n.location == "Remote"

    def test_remote_role_suffix_stripped(self) -> None:
        raw = RawScrapedJob(
            company="A",
            role="Engineer (Remote)",
            url="u",
            source="remoteok",
        )
        n = NormalizedJob.from_raw(raw)
        assert n.role == "Engineer"

    def test_greenhouse_split(self) -> None:
        raw = RawScrapedJob(
            company="A",
            role="SRE",
            url="u",
            source="Greenhouse (acme)",
        )
        n = NormalizedJob.from_raw(raw)
        assert n.source_canonical == "greenhouse"
        assert n.source_board == "acme"

    def test_default_canonical_lowers_first_segment(self) -> None:
        raw = RawScrapedJob(
            company="A",
            role="SRE",
            url="u",
            source="WeWorkRemotely/all",
        )
        n = NormalizedJob.from_raw(raw)
        assert n.source_canonical == "weworkremotely"
        assert n.source_board == ""

    def test_comp_passed_through_from_display(self) -> None:
        raw = RawScrapedJob(
            company="A",
            role="SRE",
            url="u",
            source="remoteok",
            comp_display="$150,000-$200,000",
        )
        n = NormalizedJob.from_raw(raw)
        assert n.comp == "$150,000-$200,000"

    def test_frozen(self) -> None:
        raw = RawScrapedJob(company="A", role="R", url="u", source="s")
        n = NormalizedJob.from_raw(raw)
        with pytest.raises(ValidationError):
            n.location = "elsewhere"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# EnrichedJob — frozen + model_copy + CSV round-trip
# --------------------------------------------------------------------------- #


def _enriched(**overrides: object) -> EnrichedJob:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="remoteok",
        location="Remote",
        comp_display="$150,000-$200,000",
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    return base.model_copy(update=dict(overrides))


class TestEnrichedJob:
    def test_frozen_q14(self) -> None:
        j = _enriched()
        with pytest.raises(ValidationError):
            j.fit = 8  # type: ignore[misc]

    def test_fit_bounds_enforced(self) -> None:
        # NB: pydantic v2 skips validation on model_copy(update=...) by default,
        # so bounds are only enforced at construction time, not via model_copy.
        raw = RawScrapedJob(company="A", role="R", url="u", source="s")
        n = NormalizedJob.from_raw(raw)
        with pytest.raises(ValidationError):
            EnrichedJob.from_normalized(n).model_validate(
                {**EnrichedJob.from_normalized(n).model_dump(), "fit": 11}
            )
        with pytest.raises(ValidationError):
            EnrichedJob.from_normalized(n).model_validate(
                {**EnrichedJob.from_normalized(n).model_dump(), "fit": 0}
            )

    def test_csv_row_round_trip(self) -> None:
        j = _enriched(fit=7, notes="hello")
        row = j.to_csv_row()
        j2 = EnrichedJob.from_csv_row(row)
        # Lossy: source_canonical/source_board are re-derived from Source on read
        assert j2.company == j.company
        assert j2.role == j.role
        assert j2.url == j.url
        assert j2.fit == j.fit
        assert j2.notes == j.notes
        assert j2.comp == j.comp
        assert j2.date_found == j.date_found

    def test_parse_fit_rejects_unicode_digit(self) -> None:
        """isascii guard: a Unicode digit must return None, not raise (W1.1)."""
        assert parse_fit("⁷") is None  # superscript seven

    def test_from_csv_row_tolerates_legacy_fit_suffix(self) -> None:
        """Legacy rows wrote Fit as "7/10"; the reader parses the leading int."""
        row = _enriched(fit=7).to_csv_row()
        row["Fit"] = "7/10"
        assert EnrichedJob.from_csv_row(row).fit == 7

    def test_csv_skip_reason_appended_when_skipped(self) -> None:
        j = _enriched(status="skipped", skip_reason="manually skipped")
        row = j.to_csv_row()
        assert "manually skipped" in row["Notes"]

    def test_canonical_header_is_csv_columns(self) -> None:
        assert EnrichedJob.CANONICAL_HEADER == list(
            EnrichedJob.CSV_COLUMN_TO_ATTR.keys()
        )


# --------------------------------------------------------------------------- #
# Source protocol — Q16 prep
# --------------------------------------------------------------------------- #


def test_source_protocol_runtime_checkable() -> None:
    def fake(config):  # type: ignore[no-untyped-def]
        return []

    assert isinstance(fake, Source)


def test_status_is_free_text_normalized() -> None:
    """Status is a normalized free-text string, not a closed enum."""
    raw = RawScrapedJob(
        company="Acme", role="SRE", url="https://example.com/j", source="remoteok"
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    # Newly scraped rows default to `found`; underscore/upper variants normalize.
    assert base.status == "found"
    assert base.with_updates(status="Ruled_Out").status == "ruled-out"
    # Any string is accepted (convention, not enforcement) — no ValidationError.
    assert base.with_updates(status="some-custom").status == "some-custom"


def test_scraped_row_defaults_to_found() -> None:
    """from_normalized is the scraped path; its status defaults to `found`."""
    raw = RawScrapedJob(
        company="Acme", role="SRE", url="https://example.com/j", source="remoteok"
    )
    assert EnrichedJob.from_normalized(NormalizedJob.from_raw(raw)).status == "found"


def test_blank_status_round_trips_blank() -> None:
    """A deliberately blank Status cell must stay blank on read+rewrite.

    Coercing blank -> `found` would relabel rows the user emptied on purpose
    when backfill/prune rewrites them. Only the scrape path defaults to found.
    """
    j = _enriched(fit=7).with_updates(status="")
    assert j.status == ""
    row = j.to_csv_row()
    assert row["Status"] == ""
    assert EnrichedJob.from_csv_row(row).status == ""
    # Whitespace-only cells also normalize to blank, not `found`.
    assert EnrichedJob.from_csv_row({**row, "Status": "   "}).status == ""


def test_closed_is_a_recommended_job_status() -> None:
    """`closed` is a shipped prune default, so it belongs in the recommended set."""
    from daily_driver.plugins.job_search.scraper.models import (
        JOBS_RECOMMENDED_STATUSES,
    )

    assert "closed" in JOBS_RECOMMENDED_STATUSES
