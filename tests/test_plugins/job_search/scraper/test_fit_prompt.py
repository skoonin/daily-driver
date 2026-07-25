"""Fit/notes prompt: comp weighting and the configurable description cap.

Pins the two comp-visibility fixes: the system prompt weighs stated
compensation (above location except for home-city roles), and the per-job
user prompt carries the parsed Comp value plus a description cut at
``max_description_words`` instead of a hardcoded 500 words.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from daily_driver.plugins.job_search.config import EnrichmentConfig
from daily_driver.plugins.job_search.scraper.enrichment.llm import (
    _build_fit_notes_system,
    _build_fit_notes_user,
)
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    NormalizedJob,
    RawScrapedJob,
)

_CONTEXT = "CANDIDATE BACKGROUND: ten years SRE, deep Kubernetes and Terraform."


def _job(**overrides: Any) -> EnrichedJob:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="remoteok",
        location="Remote",
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    return base.model_copy(update=dict(overrides))


# --- System prompt: comp as a weighted factor --------------------------------


def test_system_prompt_weighs_comp_between_experience_and_location() -> None:
    prompt = _build_fit_notes_system("SRE", "loc", "Vancouver", (), _CONTEXT)
    exp = prompt.index("(1) experience match")
    comp = prompt.index("(2) compensation")
    loc = prompt.index("(3) location fit")
    seniority = prompt.index("(4) seniority and track match")
    assert exp < comp < loc < seniority


def test_system_prompt_home_city_exception_names_the_city() -> None:
    prompt = _build_fit_notes_system("SRE", "loc", "Vancouver", (), _CONTEXT)
    assert "roles located in Vancouver" in prompt
    assert "location fit outweighs compensation" in prompt


def test_system_prompt_never_penalizes_missing_comp() -> None:
    prompt = _build_fit_notes_system("SRE", "loc", "Vancouver", (), _CONTEXT)
    assert "never penalize a job for not stating pay" in prompt


def test_no_context_fallback_prompt_unchanged() -> None:
    """The thin fallback (no context.md) keeps its role/company/location text."""
    prompt = _build_fit_notes_system("SRE", "loc", "Vancouver")
    assert "compensation" not in prompt
    assert "based on role/company/location" in prompt


# --- User prompt: comp line + description cap --------------------------------


def test_user_prompt_states_comp_when_present() -> None:
    job = _job(comp="$150,000–$180,000/yr CAD")
    prompt = _build_fit_notes_user(job, 2000)
    assert "Stated compensation: $150,000–$180,000/yr CAD" in prompt


def test_user_prompt_omits_comp_line_when_blank() -> None:
    prompt = _build_fit_notes_user(_job(comp=""), 2000)
    assert "Stated compensation" not in prompt


def test_user_prompt_truncates_at_the_configured_cap() -> None:
    words = [f"w{i}" for i in range(150)]
    job = _job(description_text=" ".join(words))
    prompt = _build_fit_notes_user(job, 100)
    assert "w99" in prompt
    assert "w100" not in prompt
    assert prompt.endswith("...")


def test_user_prompt_keeps_description_at_or_under_the_cap() -> None:
    words = [f"w{i}" for i in range(100)]
    job = _job(description_text=" ".join(words))
    prompt = _build_fit_notes_user(job, 100)
    assert "w99" in prompt
    assert not prompt.endswith("...")


# --- Config -------------------------------------------------------------------


def test_max_description_words_default() -> None:
    assert EnrichmentConfig().max_description_words == 2000


def test_max_description_words_accepts_the_floor() -> None:
    cfg = EnrichmentConfig.model_validate({"max_description_words": 100})
    assert cfg.max_description_words == 100


def test_max_description_words_rejects_below_floor() -> None:
    with pytest.raises(ValidationError):
        EnrichmentConfig.model_validate({"max_description_words": 99})
