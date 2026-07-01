"""The Remote column on EnrichedJob (task #6).

A new ``remote`` field ("remote" | "hybrid" | "onsite" | "") sits in the CSV
immediately after Location. Old 14-column files (no Remote column) read as a
blank Remote; a backfill rewrite adds it. Hand-entered values are preserved
verbatim on read (tolerant passthrough).
"""

from __future__ import annotations

from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    NormalizedJob,
    RawScrapedJob,
)


def _enriched(**overrides: object) -> EnrichedJob:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="remoteok",
        location="Berlin, Germany",
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    return base.model_copy(update=dict(overrides))


class TestRemoteColumnSchema:
    def test_remote_column_immediately_after_location(self) -> None:
        header = EnrichedJob.CANONICAL_HEADER
        loc_idx = header.index("Location")
        assert header[loc_idx + 1] == "Remote"

    def test_header_is_fourteen_columns(self) -> None:
        assert len(EnrichedJob.CANONICAL_HEADER) == 14

    def test_remote_defaults_blank(self) -> None:
        assert _enriched().remote == ""

    def test_remote_round_trips_through_csv(self) -> None:
        j = _enriched(remote="hybrid")
        row = j.to_csv_row()
        assert row["Remote"] == "hybrid"
        assert EnrichedJob.from_csv_row(row).remote == "hybrid"

    def test_old_14_column_row_reads_blank_remote(self) -> None:
        # A row dict from a legacy file simply has no "Remote" key.
        row = _enriched().to_csv_row()
        del row["Remote"]
        assert EnrichedJob.from_csv_row(row).remote == ""

    def test_hand_entered_value_preserved_verbatim(self) -> None:
        # Tolerant passthrough: a non-canonical hand-entered string survives read.
        row = _enriched().to_csv_row()
        row["Remote"] = "remote (US hours)"
        assert EnrichedJob.from_csv_row(row).remote == "remote (US hours)"
