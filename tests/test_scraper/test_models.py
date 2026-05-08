"""Isolated tests for scraper.models — no callers wired up yet (K1)."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from daily_driver.scraper.models import (
    Comp,
    EnrichedJob,
    JobDetails,
    JobStatus,
    NormalizedJob,
    RawScrapedJob,
    Source,
)

# --------------------------------------------------------------------------- #
# Comp
# --------------------------------------------------------------------------- #


class TestComp:
    def test_unknown_comp_is_empty(self) -> None:
        c = Comp()
        assert not c.is_known
        assert c.min_usd is None and c.max_usd is None
        assert str(c) == ""

    def test_unknown_preserves_raw_display(self) -> None:
        c = Comp(raw_display="competitive")
        assert str(c) == "competitive"

    def test_currency_required_when_amount_set(self) -> None:
        with pytest.raises(ValidationError):
            Comp(min_native=100_000)

    def test_min_gt_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Comp(min_native=200_000, max_native=100_000, currency="USD")

    def test_min_usd_conversion(self) -> None:
        c = Comp(min_native=100_000, max_native=120_000, currency="CAD")
        assert c.min_usd == int(100_000 * 0.73)
        assert c.max_usd == int(120_000 * 0.73)

    def test_meets_threshold_unknown_passes(self) -> None:
        ok, reason = Comp().meets_threshold(150_000)
        assert ok and reason == ""

    def test_meets_threshold_below(self) -> None:
        c = Comp(min_native=80_000, max_native=100_000, currency="USD")
        ok, reason = c.meets_threshold(150_000)
        assert not ok and "below comp threshold" in reason

    def test_meets_threshold_at_or_above(self) -> None:
        c = Comp(min_native=150_000, max_native=200_000, currency="USD")
        ok, _ = c.meets_threshold(150_000)
        assert ok

    def test_str_range(self) -> None:
        c = Comp(min_native=150_000, max_native=200_000, currency="USD")
        assert str(c) == "$150,000-$200,000/yr"

    def test_str_single_amount(self) -> None:
        c = Comp(min_native=100_000, max_native=100_000, currency="USD")
        assert str(c) == "$100,000/yr"

    def test_str_non_usd(self) -> None:
        c = Comp(min_native=120_000, max_native=160_000, currency="CAD")
        assert str(c) == "CAD 120,000-CAD 160,000/yr"

    def test_str_period_suffix(self) -> None:
        c = Comp(min_native=50, max_native=80, currency="USD", period="hour")
        assert str(c).endswith("/hr")

    def test_frozen(self) -> None:
        c = Comp()
        with pytest.raises(ValidationError):
            c.raw_display = "mutated"  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            Comp(min_native=100_000, currency="USD", bogus="x")  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "display, currency, min_native, max_native",
        [
            ("$150,000-$200,000", "USD", 150_000, 200_000),
            ("CAD 120,000-160,000/yr", "CAD", 120_000, 160_000),
            ("£90,000-£110,000", "GBP", 90_000, 110_000),
        ],
    )
    def test_parse_known(
        self,
        display: str,
        currency: str,
        min_native: int,
        max_native: int,
    ) -> None:
        c = Comp.parse(display)
        assert c.is_known
        assert c.currency == currency
        assert c.min_native == min_native
        assert c.max_native == max_native
        assert c.raw_display == display

    def test_parse_unparseable_keeps_display(self) -> None:
        c = Comp.parse("competitive")
        assert not c.is_known
        assert c.raw_display == "competitive"

    def test_parse_empty(self) -> None:
        assert not Comp.parse("").is_known

    @pytest.mark.parametrize(
        "display, period",
        [
            ("$50-$80/hr", "hour"),
            ("$8,000-$10,000/mo", "month"),
            ("$150,000-$200,000/yr", "year"),
        ],
    )
    def test_parse_period(self, display: str, period: str) -> None:
        c = Comp.parse(display)
        assert c.is_known
        assert c.period == period

    def test_only_max_set_is_valid(self) -> None:
        c = Comp(max_native=100_000, currency="USD")
        assert c.is_known
        assert c.min_native is None and c.max_native == 100_000
        assert c.min_usd is None and c.max_usd == 100_000

    def test_meets_threshold_only_min_set_fails_open(self) -> None:
        # min_native set, max_native None: max_usd is None → fails open.
        c = Comp(min_native=80_000, currency="USD")
        ok, reason = c.meets_threshold(150_000)
        assert ok and reason == ""

    # K4: comp parsing moved into Comp.parse; cover currency/range edge cases
    # that previously lived in test_filters.py's TestCompParsing.
    @pytest.mark.parametrize(
        "display, currency, min_native, max_native",
        [
            ("$150K-$200K", "USD", 150_000, 200_000),  # K shorthand
            ("$1.5M", "USD", 1_500_000, 1_500_000),  # M shorthand, single
            ("USD 150,000", "USD", 150_000, 150_000),  # ISO code prefix, single
            ("CA$120,000-CA$160,000", "CAD", 120_000, 160_000),  # CA$ symbol
            ("€80,000-€100,000", "EUR", 80_000, 100_000),
            ("£90K-£110K", "GBP", 90_000, 110_000),
            ("200,000-150,000", "USD", 150_000, 200_000),  # swap min>max
        ],
    )
    def test_parse_currency_and_amount_variants(
        self,
        display: str,
        currency: str,
        min_native: int,
        max_native: int,
    ) -> None:
        c = Comp.parse(display)
        assert c.is_known
        assert c.currency == currency
        assert c.min_native == min_native
        assert c.max_native == max_native

    def test_parse_iso_code_takes_precedence_over_symbol(self) -> None:
        # When both an ISO code and a symbol appear, ISO wins.
        c = Comp.parse("$150,000 USD")
        assert c.currency == "USD"
        c = Comp.parse("CAD $120,000")
        assert c.currency == "CAD"

    def test_parse_em_dash_separator(self) -> None:
        c = Comp.parse("$150,000–$200,000")
        assert c.is_known
        assert c.min_native == 150_000 and c.max_native == 200_000


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

    def test_comp_parsed_from_display(self) -> None:
        raw = RawScrapedJob(
            company="A",
            role="SRE",
            url="u",
            source="remoteok",
            comp_display="$150,000-$200,000",
        )
        n = NormalizedJob.from_raw(raw)
        assert n.comp.is_known and n.comp.currency == "USD"

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

    def test_with_fit_returns_new_instance(self) -> None:
        j = _enriched()
        j2 = j.with_fit(8, "good match")
        assert j2 is not j
        assert j2.fit == 8 and j2.notes == "good match"
        assert j.fit is None

    def test_with_details_only_fills_blanks(self) -> None:
        j = _enriched(description_text="existing")
        details = JobDetails(description_text="new", posted_date=dt.date(2026, 1, 1))
        j2 = j.with_details(details)
        # Existing description not overwritten.
        assert j2.description_text == "existing"
        assert j2.posted_date == dt.date(2026, 1, 1)

    def test_with_details_does_not_overwrite_existing_posted_date(self) -> None:
        j = _enriched(posted_date=dt.date(2026, 1, 1))
        details = JobDetails(posted_date=dt.date(2026, 5, 5))
        j2 = j.with_details(details)
        assert j2.posted_date == dt.date(2026, 1, 1)

    def test_with_details_does_not_overwrite_known_comp(self) -> None:
        j = _enriched()  # comp parsed from "$150,000-$200,000"
        assert j.comp.is_known
        new_comp = Comp(min_native=300_000, max_native=400_000, currency="USD")
        j2 = j.with_details(JobDetails(comp=new_comp))
        assert j2.comp == j.comp

    def test_with_details_fills_unknown_comp(self) -> None:
        raw = RawScrapedJob(company="A", role="R", url="u", source="s")
        j = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
        assert not j.comp.is_known
        details = JobDetails(
            comp=Comp(min_native=120_000, max_native=140_000, currency="USD"),
        )
        j2 = j.with_details(details)
        assert j2.comp.is_known and j2.comp.currency == "USD"

    def test_fit_bounds_enforced(self) -> None:
        # NB: pydantic v2 skips validation on model_copy(update=...) by default,
        # so bounds are only enforced at construction time. with_fit goes via
        # model_copy and is therefore not the right surface to test bounds.
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
        # Lossy: source_canonical/source_board re-derived; product default if blank
        assert j2.company == j.company
        assert j2.role == j.role
        assert j2.url == j.url
        assert j2.fit == j.fit
        assert j2.notes == j.notes
        # Comp round-trips structurally; raw_display is lossy (str(c) vs original).
        assert j2.comp.min_native == j.comp.min_native
        assert j2.comp.max_native == j.comp.max_native
        assert j2.comp.currency == j.comp.currency
        assert j2.date_found == j.date_found

    def test_csv_skip_reason_appended_when_skipped(self) -> None:
        j = _enriched(status=JobStatus.SKIPPED, skip_reason="below comp")
        row = j.to_csv_row()
        assert "below comp" in row["Notes"]

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
