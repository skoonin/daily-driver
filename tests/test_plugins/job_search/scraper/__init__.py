"""Scraper test helpers shared across the migrated EnrichedJob enricher tests."""

from __future__ import annotations

from typing import Any

from daily_driver.core.providers import (
    Provider,
    ProviderConfig,
    builtin_providers,
)
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    NormalizedJob,
    RawScrapedJob,
)


def make_enriched(
    *,
    company: str = "Acme",
    role: str = "SRE",
    url: str = "https://example.com/j",
    source: str = "remoteok",
    location: str = "Remote",
    comp: str = "",
    **overrides: Any,
) -> EnrichedJob:
    """Build an EnrichedJob for tests that used to hand-roll a working dict.

    ``overrides`` are applied via ``model_copy`` so callers can seed enrichment
    fields (fit, notes, description_text, status, ...) directly.
    """
    raw = RawScrapedJob(
        company=company,
        role=role,
        url=url,
        source=source,
        location=location,
        comp_display=comp,
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    return base.model_copy(update=dict(overrides))


def claude_provider(*, max_parallel: int = 4) -> Provider:
    """The built-in claude provider with a chosen concurrency cap."""
    settings = builtin_providers()["claude"].model_copy(
        update={"max_parallel": max_parallel}
    )
    return Provider("claude", settings)


def ollama_provider(*, model: str = "qwen2.5:14b", max_parallel: int = 4) -> Provider:
    """An HTTP provider shaped like the built-in ollama, with a model set."""
    return Provider(
        "ollama",
        ProviderConfig(
            base_url="http://localhost:11434/v1",
            model=model,
            max_parallel=max_parallel,
        ),
    )
