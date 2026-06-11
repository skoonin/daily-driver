"""LLM remote tier: the fit/notes call also judges remote-ness (task #6).

The existing fit/notes provider call gains a "remote" field in the JSON it
requests, gated by ``enrich_is_remote``. This adds ZERO extra LLM calls — it
piggybacks the one fit/notes call per job. Parsing is tolerant (unknown ->
leave existing) and hand-entered / heuristic values are never clobbered by a
blank or unknown LLM answer.
"""

from __future__ import annotations

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import enrich_fit_and_notes
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
        location="Berlin, Germany",
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    return base.model_copy(update=dict(overrides))


def _ctx(**enrichment: object) -> ScrapeContext:
    base: dict[str, object] = {
        "provider": "ollama",
        "model": "qwen2.5:14b",
        "max_enrich_fit": 5,
        "enrich_timeout": 5,
    }
    base.update(enrichment)
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate({"enrichment": base}),
        ai=AIConfig(),
    )


class TestRemotePromptGating:
    def test_remote_requested_in_prompt_when_toggle_on(self, monkeypatch) -> None:
        prompts: list[str] = []

        def fake(prompt, *a, **k):
            prompts.append(prompt)
            return '{"fit": 7, "notes": "k8s", "remote": "hybrid"}'

        monkeypatch.setattr(ai_provider, "invoke_for", fake)
        j = _enriched(description_text="Hybrid SRE role")
        out, _stats = enrich_fit_and_notes([j], _ctx(enrich_is_remote=True), budget=5)
        assert prompts and "remote" in prompts[0].lower()
        assert out[0].remote == "hybrid"

    def test_remote_not_requested_or_written_when_toggle_off(self, monkeypatch) -> None:
        prompts: list[str] = []

        def fake(prompt, *a, **k):
            prompts.append(prompt)
            # The model returns remote anyway; with the toggle off it is ignored.
            return '{"fit": 7, "notes": "k8s", "remote": "remote"}'

        monkeypatch.setattr(ai_provider, "invoke_for", fake)
        j = _enriched(description_text="desc")
        out, _stats = enrich_fit_and_notes([j], _ctx(enrich_is_remote=False), budget=5)
        # Prompt must not request a remote field, and nothing is written.
        assert prompts and '"remote"' not in prompts[0]
        assert out[0].remote == ""

    def test_zero_extra_calls_with_toggle_on(self, monkeypatch) -> None:
        call_count = [0]

        def fake(prompt, *a, **k):
            call_count[0] += 1
            return '{"fit": 7, "notes": "k8s", "remote": "remote"}'

        monkeypatch.setattr(ai_provider, "invoke_for", fake)
        j = _enriched(description_text="desc")
        enrich_fit_and_notes([j], _ctx(enrich_is_remote=True), budget=5)
        # Exactly one call (fit/notes); remote piggybacks it, no second call.
        assert call_count[0] == 1


class TestRemoteHandEnteredPreserved:
    def test_blank_llm_answer_never_overwrites_existing(self, monkeypatch) -> None:
        def fake(prompt, *a, **k):
            return '{"fit": 7, "notes": "k8s", "remote": ""}'

        monkeypatch.setattr(ai_provider, "invoke_for", fake)
        j = _enriched(remote="onsite", description_text="desc")
        out, _stats = enrich_fit_and_notes([j], _ctx(enrich_is_remote=True), budget=5)
        assert out[0].remote == "onsite"  # hand-entered value wins

    def test_unknown_llm_answer_leaves_existing(self, monkeypatch) -> None:
        def fake(prompt, *a, **k):
            return '{"fit": 7, "notes": "k8s", "remote": "maybe?"}'

        monkeypatch.setattr(ai_provider, "invoke_for", fake)
        j = _enriched(remote="remote", description_text="desc")
        out, _stats = enrich_fit_and_notes([j], _ctx(enrich_is_remote=True), budget=5)
        assert out[0].remote == "remote"  # unrecognized -> leave existing

    def test_llm_fills_blank_remote(self, monkeypatch) -> None:
        def fake(prompt, *a, **k):
            return '{"fit": 7, "notes": "k8s", "remote": "onsite"}'

        monkeypatch.setattr(ai_provider, "invoke_for", fake)
        j = _enriched(remote="", description_text="desc")
        out, _stats = enrich_fit_and_notes([j], _ctx(enrich_is_remote=True), budget=5)
        assert out[0].remote == "onsite"  # LLM fills a blank

    def test_llm_refines_heuristic_remote_to_hybrid(self, monkeypatch) -> None:
        # The heuristic can only say "remote"; the LLM may refine it to "hybrid".
        def fake(prompt, *a, **k):
            return '{"fit": 7, "notes": "k8s", "remote": "hybrid"}'

        monkeypatch.setattr(ai_provider, "invoke_for", fake)
        j = _enriched(remote="remote", description_text="desc")
        out, _stats = enrich_fit_and_notes([j], _ctx(enrich_is_remote=True), budget=5)
        assert out[0].remote == "hybrid"
