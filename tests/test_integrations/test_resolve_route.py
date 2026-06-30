"""Unit tests for the centralized AI route resolver.

`resolve_route(ai, *, task, domain_cfg=None, cli_provider=None, cli_model=None)`
walks provider and model INDEPENDENTLY through the chain
`task/phase override -> domain default -> global ai default -> hardcoded claude`,
with CLI flags outranking all config.
"""

from __future__ import annotations

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.config import EnrichmentConfig

# ---------------------------------------------------------------------------
# Core tasks: ai.<task> -> ai global -> claude
# ---------------------------------------------------------------------------


def test_all_unset_resolves_to_claude_none() -> None:
    provider, model = ai_provider.resolve_route(AIConfig(), task="summary")
    assert provider == "claude"
    assert model is None


def test_global_default_wins_when_task_unset() -> None:
    ai = AIConfig.model_validate({"provider": "ollama", "model": "phi4"})
    provider, model = ai_provider.resolve_route(ai, task="summary")
    assert provider == "ollama"
    assert model == "phi4"


def test_task_override_beats_global() -> None:
    ai = AIConfig.model_validate(
        {"provider": "ollama", "summary": {"provider": "claude", "model": "sonnet"}}
    )
    provider, model = ai_provider.resolve_route(ai, task="summary")
    assert provider == "claude"
    assert model == "sonnet"


def test_independent_provider_and_model_walk() -> None:
    """Provider comes from global, model from the task block — walked apart.

    Regression for gap 10: the claude path must honor `ai.summary.model` even
    though `ai.summary.provider` is unset.
    """
    ai = AIConfig.model_validate(
        {"provider": "ollama", "model": "phi4", "summary": {"model": "sonnet"}}
    )
    provider, model = ai_provider.resolve_route(ai, task="summary")
    assert provider == "ollama"  # from global (summary.provider unset)
    assert model == "sonnet"  # from task block, not global "phi4"


def test_gap10_claude_path_honors_summary_model() -> None:
    """All-claude config with only `ai.summary.model` set resolves that model."""
    ai = AIConfig.model_validate({"summary": {"model": "sonnet"}})
    provider, model = ai_provider.resolve_route(ai, task="summary")
    assert provider == "claude"
    assert model == "sonnet"


def test_voice_update_task_routable() -> None:
    ai = AIConfig.model_validate({"voice_update": {"provider": "ollama"}})
    provider, model = ai_provider.resolve_route(ai, task="voice_update")
    assert provider == "ollama"


# ---------------------------------------------------------------------------
# CLI flags outrank all config
# ---------------------------------------------------------------------------


def test_cli_provider_overrides_config() -> None:
    ai = AIConfig.model_validate({"provider": "ollama", "model": "phi4"})
    provider, model = ai_provider.resolve_route(
        ai, task="summary", cli_provider="claude"
    )
    assert provider == "claude"
    assert model == "phi4"  # cli_model unset -> config model still walks


def test_cli_model_overrides_config() -> None:
    ai = AIConfig.model_validate({"summary": {"model": "sonnet"}})
    provider, model = ai_provider.resolve_route(ai, task="summary", cli_model="opus")
    assert provider == "claude"
    assert model == "opus"


# ---------------------------------------------------------------------------
# Enrichment phases: enrichment.<phase> -> enrichment domain -> ai global -> claude
# ---------------------------------------------------------------------------


def test_enrichment_domain_default_applies_when_phase_unset() -> None:
    enrichment = EnrichmentConfig.model_validate(
        {"provider": "ollama", "model": "phi4"}
    )
    provider, model = ai_provider.resolve_route(
        AIConfig(), task="fit_notes", domain_cfg=enrichment
    )
    assert provider == "ollama"
    assert model == "phi4"


def test_enrichment_phase_override_beats_domain() -> None:
    enrichment = EnrichmentConfig.model_validate(
        {
            "provider": "ollama",
            "model": "phi4",
            "fit_notes": {"provider": "claude", "model": "sonnet"},
        }
    )
    # The fit_notes phase override wins over the domain default.
    fn_provider, fn_model = ai_provider.resolve_route(
        AIConfig(), task="fit_notes", domain_cfg=enrichment
    )
    assert fn_provider == "claude"
    assert fn_model == "sonnet"


def test_enrichment_falls_through_to_global_then_claude() -> None:
    # Enrichment domain unset, but a global ai default exists.
    ai = AIConfig.model_validate({"provider": "ollama", "model": "phi4"})
    provider, model = ai_provider.resolve_route(
        ai, task="fit_notes", domain_cfg=EnrichmentConfig()
    )
    assert provider == "ollama"
    assert model == "phi4"

    # Nothing set anywhere -> claude terminal fallback.
    provider, model = ai_provider.resolve_route(
        AIConfig(), task="fit_notes", domain_cfg=EnrichmentConfig()
    )
    assert provider == "claude"
    assert model is None


def test_enrichment_cli_flags_outrank_everything() -> None:
    enrichment = EnrichmentConfig.model_validate(
        {"provider": "ollama", "fit_notes": {"provider": "ollama", "model": "phi4"}}
    )
    provider, model = ai_provider.resolve_route(
        AIConfig(),
        task="fit_notes",
        domain_cfg=enrichment,
        cli_provider="claude",
        cli_model="sonnet",
    )
    assert provider == "claude"
    assert model == "sonnet"
