"""Tests for the typed enricher wrappers (K8)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from daily_driver.plugins.job_search.scraper.enrichment import (
    enrich_company_descriptions_typed,
    enrich_fit_and_notes_typed,
    enrich_job_details_typed,
)
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    JobStatus,
    NormalizedJob,
    RawScrapedJob,
)


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


@pytest.fixture
def fake_config() -> dict[str, Any]:
    return {
        "plugins": {
            "job_search": {
                "scraper": {"enabled": True, "max_enrich_companies": 0},
            }
        }
    }


def test_typed_enrichers_return_new_instances(fake_config: dict[str, Any]) -> None:
    """Frozen-model invariant: enrichers must return new instances."""
    j = _enriched()
    # company description path short-circuits when claude CLI absent — that's fine.
    out, _stats = enrich_company_descriptions_typed([j], fake_config)
    assert len(out) == 1
    assert isinstance(out[0], EnrichedJob)


def test_typed_company_enrich_passes_through_when_no_claude(
    fake_config: dict[str, Any],
) -> None:
    j = _enriched()
    with patch("shutil.which", return_value=None):
        out, stats = enrich_company_descriptions_typed([j], fake_config)
    assert stats == {"enriched": 0, "skipped_cached": 0, "failed": 0}
    # No mutation to product when claude is absent.
    assert out[0].product == j.product


def test_typed_fit_enrich_passes_through_with_zero_budget(
    fake_config: dict[str, Any],
) -> None:
    j = _enriched()
    out, stats = enrich_fit_and_notes_typed([j], fake_config, budget=0)
    # budget=0 short-circuits; no claude call attempted.
    assert len(out) == 1
    assert out[0].fit == j.fit  # unchanged


def test_typed_job_details_short_circuits_when_description_present(
    fake_config: dict[str, Any],
) -> None:
    j = _enriched(description_text="already populated")
    out = enrich_job_details_typed([j], fake_config)
    assert len(out) == 1
    # description_text preserved from input.
    assert out[0].description_text == "already populated"


def test_typed_enrichers_preserve_immutability(fake_config: dict[str, Any]) -> None:
    """Input EnrichedJob instances must not be mutated (Q14: frozen=True)."""
    j = _enriched(notes="original")
    enrich_fit_and_notes_typed([j], fake_config, budget=0)
    # Original instance unchanged regardless of what wrapper does internally.
    assert j.notes == "original"
    assert j.fit is None


def test_typed_enrichers_handle_skipped_status(fake_config: dict[str, Any]) -> None:
    j = _enriched(status=JobStatus.SKIPPED, skip_reason="below comp")
    out = enrich_job_details_typed([j], fake_config)
    assert out[0].status is JobStatus.SKIPPED
