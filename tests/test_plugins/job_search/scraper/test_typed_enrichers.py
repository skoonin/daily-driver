"""Tests for the EnrichedJob enrichers (single representation, post-collapse).

The enrichers take and return ``list[EnrichedJob]``; the former dict-based
bodies + typed wrappers were folded into these. These pin the model-level
contract: new frozen instances out, immutable inputs, skipped-status handling.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import (
    enrich_company_descriptions,
    enrich_fit_and_notes,
    enrich_job_details,
)
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
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
    # Both config caps are zeroed so the enrichers make no provider calls even
    # when a test leaves budget=None (use-config-default). Without
    # max_enrich_fit=0 the fit enricher makes real claude CLI calls on dev
    # machines where `claude` is on PATH.
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True},
                "enrichment": {"max_enrich_companies": 0, "max_enrich_fit": 0},
            }
        )
    )


def test_enrichers_return_enriched_job_list(fake_config: ScrapeContext) -> None:
    """Company enrich returns a list of EnrichedJob (frozen-model invariant)."""
    j = _enriched()
    out, _stats = enrich_company_descriptions([j], fake_config)
    assert len(out) == 1
    assert isinstance(out[0], EnrichedJob)


def test_company_enrich_passes_through_when_no_claude(
    fake_config: ScrapeContext,
) -> None:
    j = _enriched()
    with patch("shutil.which", return_value=None):
        out, stats = enrich_company_descriptions([j], fake_config)
    assert stats == {"enriched": 0, "skipped_cached": 0, "failed": 0}
    # No mutation to product when claude is absent.
    assert out[0].product == j.product


def test_fit_enrich_passes_through_with_zero_budget(
    fake_config: ScrapeContext,
) -> None:
    j = _enriched()
    out, stats = enrich_fit_and_notes([j], fake_config, budget=0)
    # Explicit budget 0 => no jobs enriched, no provider call.
    assert len(out) == 1
    assert out[0].fit == j.fit  # unchanged


def test_fit_enrich_score_survives_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scored fit must reach the returned EnrichedJob as a bare int (W1.1)."""
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "model": "qwen2.5:14b",
                    "max_enrich_fit": 5,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda *a, **k: '{"fit": 7, "notes": "k8s"}'
    )
    j = _enriched(description_text="Kubernetes-heavy SRE role")
    out, _stats = enrich_fit_and_notes([j], ctx, budget=5)
    assert out[0].fit == 7
    assert out[0].notes == "k8s"


def test_fit_budget_zero_makes_no_calls_despite_config_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit budget=0 means NO calls -- it must not fall back to the
    config cap (wave 2 passes the remaining shared budget, which can be 0)."""
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "max_enrich_fit": 5,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        ai_provider,
        "invoke_for",
        lambda *a, **k: calls.append("x") or '{"fit": 7, "notes": "n"}',
    )
    jobs = [_enriched(url=f"https://x/{i}") for i in range(3)]
    out, stats = enrich_fit_and_notes(jobs, ctx, budget=0)
    assert calls == []
    assert stats["skipped_budget"] == 3
    assert all(o.fit == j.fit for o, j in zip(out, jobs))


def test_company_budget_zero_makes_no_calls_despite_config_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "max_enrich_companies": 5,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        ai_provider,
        "invoke_for",
        lambda *a, **k: calls.append("x") or "Builds widgets",
    )
    jobs = [_enriched(url=f"https://x/{i}") for i in range(2)]
    out, stats = enrich_company_descriptions(jobs, ctx, budget=0)
    assert calls == []
    assert stats["enriched"] == 0


def test_fit_budget_none_uses_config_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """budget=None (the default) reads the config cap; the cap is enforced."""
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "max_enrich_fit": 1,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        ai_provider,
        "invoke_for",
        lambda *a, **k: calls.append("x") or '{"fit": 7, "notes": "n"}',
    )
    jobs = [
        _enriched(url="https://x/1", description_text="d"),
        _enriched(url="https://x/2", description_text="d"),
    ]
    _out, stats = enrich_fit_and_notes(jobs, ctx, budget=None)
    assert len(calls) == 1
    assert stats["skipped_budget"] == 1


def test_location_summary_states_listed_countries_are_acceptable() -> None:
    """The fit prompt must SAY a job in a configured country is acceptable --
    a bare country list next to "Based in: <home>" reads as candidate metadata
    and models then score every non-home-country job as a location mismatch
    (observed live: Barcelona scored as mismatch with ES configured)."""
    from daily_driver.plugins.job_search.scraper.enrichment.llm import (
        _location_summary,
    )

    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "locations": {
                    "home_city": "Vancouver, BC",
                    "remote": True,
                    "countries": ["ES", "CA"],
                },
            }
        )
    )
    summary = _location_summary(ctx)
    assert "Spain" in summary
    assert "NOT a location mismatch" in summary
    assert "remote roles are acceptable" in summary


def test_fit_parses_markdown_fenced_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local models fence their JSON in markdown despite the strict-JSON
    prompt (observed live with qwen2.5:32b); the parser must peel the fence
    instead of failing the job."""
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "max_enrich_fit": 5,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    fenced = '```json\n{"fit": 6, "notes": "aligned with SRE roles"}\n```'
    monkeypatch.setattr(ai_provider, "invoke_for", lambda *a, **k: fenced)
    j = _enriched(url="https://x/1", description_text="d")
    out, stats = enrich_fit_and_notes([j], ctx)
    assert out[0].fit == 6
    assert out[0].notes == "aligned with SRE roles"
    assert stats["failed"] == 0


def test_fit_parses_prose_wrapped_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "max_enrich_fit": 5,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    wrapped = (
        'Here is the assessment:\n{"fit": 8, "notes": "strong match"}\nHope that helps.'
    )
    monkeypatch.setattr(ai_provider, "invoke_for", lambda *a, **k: wrapped)
    j = _enriched(url="https://x/1", description_text="d")
    out, stats = enrich_fit_and_notes([j], ctx)
    assert out[0].fit == 8
    assert stats["failed"] == 0


def test_fit_truly_non_json_still_counted_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "max_enrich_fit": 5,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda *a, **k: "I cannot assess this role."
    )
    j = _enriched(url="https://x/1", description_text="d")
    out, stats = enrich_fit_and_notes([j], ctx)
    assert out[0].fit == j.fit
    assert stats["failed"] == 1


def test_fit_on_planned_reports_budget_capped_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The planner reports the post-budget call count so progress bars show the
    real denominator (5 eligible, cap 2 -> planned 2)."""
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "max_enrich_fit": 2,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda *a, **k: '{"fit": 7, "notes": "n"}'
    )
    planned: list[int] = []
    jobs = [_enriched(url=f"https://x/{i}", description_text="d") for i in range(5)]
    enrich_fit_and_notes(jobs, ctx, on_planned=planned.append)
    assert planned == [2]


def test_company_on_planned_reports_budget_capped_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "max_enrich_companies": 1,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig(),
    )
    monkeypatch.setattr(ai_provider, "invoke_for", lambda *a, **k: "Builds widgets")
    planned: list[int] = []
    jobs = [
        _enriched(url="https://x/1", company="A"),
        _enriched(url="https://x/2", company="B"),
    ]
    enrich_company_descriptions(jobs, ctx, on_planned=planned.append)
    assert planned == [1]


def test_job_details_short_circuits_when_description_present(
    fake_config: ScrapeContext,
) -> None:
    j = _enriched(description_text="already populated")
    out, stats = enrich_job_details([j], fake_config)
    assert len(out) == 1
    # description_text preserved from input.
    assert out[0].description_text == "already populated"
    assert stats["total"] == 1


def test_enrichers_preserve_individual_immutability(
    fake_config: ScrapeContext,
) -> None:
    """The original frozen instances must not be mutated (only list slots)."""
    j = _enriched(notes="original")
    enrich_fit_and_notes([j], fake_config, budget=0)
    # Original instance unchanged regardless of what the enricher does.
    assert j.notes == "original"
    assert j.fit is None


def test_enrichers_handle_skipped_status(fake_config: ScrapeContext) -> None:
    j = _enriched(status="skipped", skip_reason="manually skipped")
    out, _stats = enrich_job_details([j], fake_config)
    assert out[0].status == "skipped"


# ---------------------------------------------------------------------------
# enrich_product toggle: gate product vs gd_rating writes independently.
# ---------------------------------------------------------------------------


def _company_ctx(**enrichment: object) -> ScrapeContext:
    base = {"max_enrich_companies": 50, "enrich_fit": False, "enrich_notes": False}
    base.update(enrichment)
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {"scraper": {"enabled": True}, "enrichment": base}
        ),
        ai=AIConfig.model_validate({"claude": {"max_parallel": 1}}),
    )


def test_enrich_product_off_skips_product_write_keeps_gd() -> None:
    """enrich_product=False writes no product, gd_rating still fills."""
    j = _enriched(comp_display="")
    ctx = _company_ctx(enrich_product=False, enrich_gd_rating=True)
    calls: list[str] = []

    def fake_invoke(prompt, *, provider, model, ai, timeout):
        calls.append(prompt)
        return "4.3"

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            out, _stats = enrich_company_descriptions([j], ctx)

    assert len(calls) == 1  # one call per company (gd only)
    assert "Glassdoor" in calls[0] and "build or do" not in calls[0]
    assert out[0].product == j.product  # unchanged (not written)
    assert out[0].gd_rating == "4.3"


def test_both_product_and_gd_off_skips_phase_entirely() -> None:
    """Both toggles off => no company-phase LLM calls at all."""
    j = _enriched()
    ctx = _company_ctx(enrich_product=False, enrich_gd_rating=False)

    def fake_invoke(prompt, *, provider, model, ai, timeout):
        raise AssertionError("no company call expected when both toggles off")

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            out, stats = enrich_company_descriptions([j], ctx)

    assert out[0].product == j.product
    assert stats == {"enriched": 0, "skipped_cached": 0, "failed": 0}


def test_enrich_product_default_writes_both() -> None:
    """Default (both on) writes product and gd_rating from one 2-line response."""
    j = _enriched(comp_display="")
    ctx = _company_ctx()

    def fake_invoke(prompt, *, provider, model, ai, timeout):
        return "Acme builds widgets\n4.1"

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            out, _stats = enrich_company_descriptions([j], ctx)

    assert out[0].product == "Acme builds widgets"
    assert out[0].gd_rating == "4.1"


# ---------------------------------------------------------------------------
# GD parse miss: log the miss, and in GD-only mode count it as a failure
# (finding 2). GD-only enriched stat counts any written field (finding 3).
# ---------------------------------------------------------------------------


def test_gd_parse_miss_logged(caplog) -> None:
    """A non-empty, non-'unknown' line that yields no rating logs the miss."""
    import logging

    j = _enriched(comp_display="")
    ctx = _company_ctx(enrich_product=False, enrich_gd_rating=True)

    def fake_invoke(prompt, *, provider, model, ai, timeout):
        return "I am not able to determine that"  # no float, no [0-5].[0-9]

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            with caplog.at_level(logging.INFO, logger="daily_driver"):
                out, _stats = enrich_company_descriptions([j], ctx)

    misses = [
        r.getMessage()
        for r in caplog.records
        if "gd rating" in r.getMessage().lower() and "Acme" in r.getMessage()
    ]
    assert misses, f"expected a GD parse-miss log, got: {caplog.records}"
    assert out[0].gd_rating == ""  # nothing usable extracted


def test_gd_only_parse_miss_counts_as_failure() -> None:
    """In GD-only mode, a parse miss produced nothing useful -> stats.failed."""
    j = _enriched(comp_display="")
    ctx = _company_ctx(enrich_product=False, enrich_gd_rating=True)

    def fake_invoke(prompt, *, provider, model, ai, timeout):
        return "no idea, sorry"  # no parseable rating

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            _out, stats = enrich_company_descriptions([j], ctx)

    assert stats["failed"] == 1
    assert stats["enriched"] == 0


def test_gd_only_unknown_is_not_a_failure() -> None:
    """An honest 'unknown' is a valid answer, not a failure."""
    j = _enriched(comp_display="")
    ctx = _company_ctx(enrich_product=False, enrich_gd_rating=True)

    def fake_invoke(prompt, *, provider, model, ai, timeout):
        return "unknown"

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            out, stats = enrich_company_descriptions([j], ctx)

    assert stats["failed"] == 0
    assert out[0].gd_rating == "unknown"


def test_gd_only_successful_rating_counts_as_enriched() -> None:
    """A working GD-only run reports enriched == number of companies (finding 3)."""
    jobs = [
        _enriched(company="Acme", url="https://example.com/a", comp_display=""),
        _enriched(company="Beta", url="https://example.com/b", comp_display=""),
    ]
    ctx = _company_ctx(enrich_product=False, enrich_gd_rating=True)

    def fake_invoke(prompt, *, provider, model, ai, timeout):
        return "4.2"

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            out, stats = enrich_company_descriptions(jobs, ctx)

    assert stats["enriched"] == 2
    assert all(j.gd_rating == "4.2" for j in out)
