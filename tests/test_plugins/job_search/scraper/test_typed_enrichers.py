"""Tests for the typed enricher wrappers (K8)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.config import JobSearchPlugin
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
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext


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
def fake_config() -> ScrapeContext:
    # Both enrichment budgets are zeroed: enrich_fit_and_notes / company
    # treat budget<=0 as "unset, use config default" (llm.py:719,218), so a
    # caller passing budget=0 still enriches at the config default unless the
    # config itself is 0. Without max_enrich_fit=0 the fit enricher makes real
    # claude CLI calls on dev machines where `claude` is on PATH.
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True},
                "enrichment": {"max_enrich_companies": 0, "max_enrich_fit": 0},
            }
        )
    )


def test_typed_enrichers_return_new_instances(fake_config: ScrapeContext) -> None:
    """Frozen-model invariant: enrichers must return new instances."""
    j = _enriched()
    # company description path short-circuits when claude CLI absent — that's fine.
    out, _stats = enrich_company_descriptions_typed([j], fake_config)
    assert len(out) == 1
    assert isinstance(out[0], EnrichedJob)


def test_typed_company_enrich_passes_through_when_no_claude(
    fake_config: ScrapeContext,
) -> None:
    j = _enriched()
    with patch("shutil.which", return_value=None):
        out, stats = enrich_company_descriptions_typed([j], fake_config)
    assert stats == {"enriched": 0, "skipped_cached": 0, "failed": 0}
    # No mutation to product when claude is absent.
    assert out[0].product == j.product


def test_typed_fit_enrich_passes_through_with_zero_budget(
    fake_config: ScrapeContext,
) -> None:
    j = _enriched()
    out, stats = enrich_fit_and_notes_typed([j], fake_config, budget=0)
    # Effective budget 0 (param 0 + config max_enrich_fit 0) => no jobs
    # enriched, no claude call.
    assert len(out) == 1
    assert out[0].fit == j.fit  # unchanged


def test_typed_fit_enrich_score_survives_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scored fit must reach the returned EnrichedJob as a bare int (W1.1)."""
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {"enrichment": {"max_enrich_fit": 5, "enrich_timeout": 5}}
        ),
        ai=AIConfig.model_validate(
            {"enrichment": {"provider": "ollama", "model": "qwen2.5:14b"}}
        ),
    )
    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda *a, **k: '{"fit": 7, "notes": "k8s"}'
    )
    j = _enriched(description_text="Kubernetes-heavy SRE role")
    out, _stats = enrich_fit_and_notes_typed([j], ctx, budget=5)
    assert out[0].fit == 7
    assert out[0].notes == "k8s"


def test_typed_job_details_short_circuits_when_description_present(
    fake_config: ScrapeContext,
) -> None:
    j = _enriched(description_text="already populated")
    out, stats = enrich_job_details_typed([j], fake_config)
    assert len(out) == 1
    # description_text preserved from input.
    assert out[0].description_text == "already populated"
    assert stats["total"] == 1


def test_typed_enrichers_preserve_immutability(fake_config: ScrapeContext) -> None:
    """Input EnrichedJob instances must not be mutated (Q14: frozen=True)."""
    j = _enriched(notes="original")
    enrich_fit_and_notes_typed([j], fake_config, budget=0)
    # Original instance unchanged regardless of what wrapper does internally.
    assert j.notes == "original"
    assert j.fit is None


def test_typed_enrichers_handle_skipped_status(fake_config: ScrapeContext) -> None:
    j = _enriched(status=JobStatus.SKIPPED, skip_reason="manually skipped")
    out, _stats = enrich_job_details_typed([j], fake_config)
    assert out[0].status is JobStatus.SKIPPED
