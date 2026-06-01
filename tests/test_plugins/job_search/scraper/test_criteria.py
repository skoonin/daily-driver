"""Config-driven criteria scanner: config model, prompt contract, and Notes fold.

The criteria scanner rides the existing fit/notes enrichment call — no new API
calls. These tests pin the three seams: the `Criterion` config model, the
`_build_fit_notes_prompt` JSON contract (which must stay byte-for-byte
unchanged when no criteria are configured), and the quiet-by-default fold that
appends meaningful criterion values to a job's Notes.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from daily_driver.plugins.job_search.config import (
    Criterion,
    EnrichmentConfig,
    JobSearchPlugin,
)
from daily_driver.plugins.job_search.scraper.enrichment import (
    _build_fit_notes_prompt,
    _fetch_fit_notes_for_job,
    _fold_criteria_values,
)
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

_CTX = ScrapeContext(plugin=JobSearchPlugin())


def _job(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company": "Acme",
        "role": "SRE",
        "location": "Remote",
        "product": "",
        "description_text": "We run Kubernetes at scale and sponsor visas.",
    }
    base.update(overrides)
    return base


# --- Config model -----------------------------------------------------------


def test_criterion_parses_from_dict() -> None:
    crit = Criterion.model_validate(
        {"label": "Sponsorship", "assess": "Does it sponsor?"}
    )
    assert crit.label == "Sponsorship"
    assert crit.assess == "Does it sponsor?"


def test_criterion_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        Criterion.model_validate(
            {"label": "X", "assess": "y", "weight": 3}  # extra key
        )


def test_criterion_strips_surrounding_whitespace() -> None:
    crit = Criterion.model_validate({"label": "  Sponsorship  ", "assess": " ask? "})
    assert crit.label == "Sponsorship"
    assert crit.assess == "ask?"


@pytest.mark.parametrize("field", ["label", "assess"])
def test_criterion_rejects_blank(field: str) -> None:
    data = {"label": "X", "assess": "y"}
    data[field] = "   "
    with pytest.raises(ValidationError):
        Criterion.model_validate(data)


@pytest.mark.parametrize("field", ["label", "assess"])
def test_criterion_rejects_multiline(field: str) -> None:
    data = {"label": "X", "assess": "y"}
    data[field] = "a\nb"
    with pytest.raises(ValidationError):
        Criterion.model_validate(data)


def test_enrichment_criteria_rejects_duplicate_labels() -> None:
    with pytest.raises(ValidationError):
        EnrichmentConfig.model_validate(
            {
                "criteria": [
                    {"label": "Sponsorship", "assess": "a"},
                    {"label": "Sponsorship", "assess": "b"},
                ]
            }
        )


def test_enrichment_criteria_default_empty() -> None:
    cfg = EnrichmentConfig()
    assert cfg.criteria == []


def test_enrichment_criteria_parse_list() -> None:
    cfg = EnrichmentConfig.model_validate(
        {"criteria": [{"label": "Clearance", "assess": "Security clearance required?"}]}
    )
    assert len(cfg.criteria) == 1
    assert cfg.criteria[0].label == "Clearance"


# --- Prompt contract --------------------------------------------------------


def test_prompt_base_contract_no_criteria_no_context() -> None:
    """Base prompt: fit/notes-only JSON, no criteria key, no tech-stack ask."""
    prompt = _build_fit_notes_prompt(_job(), "SRE", "Based in: Vancouver", "Vancouver")
    assert '"criteria"' not in prompt
    assert '{"fit": <integer 1-10>, "notes": "<one line, max 20 words>"}' in prompt
    assert "Do not list the tech stack" in prompt
    # No context => fit falls back to role/company/location signal.
    assert "based on role/company/location" in prompt


def test_prompt_empty_list_equals_default() -> None:
    job = _job()
    assert _build_fit_notes_prompt(job, "SRE", "loc", "Van") == _build_fit_notes_prompt(
        job, "SRE", "loc", "Van", []
    )


def test_prompt_injects_context_and_weights_experience() -> None:
    ctx = "CANDIDATE BACKGROUND: ten years SRE, deep Kubernetes and Terraform."
    prompt = _build_fit_notes_prompt(_job(), "SRE", "loc", "Van", (), ctx)
    assert "CANDIDATE CONTEXT" in prompt
    assert ctx in prompt
    # With context, fit emphasizes experience match over bare role/location.
    assert "experience match" in prompt


def test_prompt_omits_context_block_when_absent() -> None:
    prompt = _build_fit_notes_prompt(_job(), "SRE", "loc", "Van")
    assert "CANDIDATE CONTEXT" not in prompt
    assert "experience match" not in prompt


def test_prompt_with_criteria_names_each_and_extends_json() -> None:
    criteria = [
        Criterion(label="Sponsorship", assess="Does it sponsor work visas?"),
        Criterion(label="Clearance", assess="Is a security clearance required?"),
    ]
    prompt = _build_fit_notes_prompt(_job(), "SRE", "loc", "Van", criteria)
    assert '"criteria"' in prompt
    for c in criteria:
        assert c.label in prompt
        assert c.assess in prompt


# --- Fold into Notes --------------------------------------------------------

_CRITERIA = [
    Criterion(label="Sponsorship", assess="Does it sponsor?"),
    Criterion(label="Clearance", assess="Clearance required?"),
]


def test_fold_appends_meaningful_value() -> None:
    out = _fold_criteria_values(
        "Kubernetes shop", _CRITERIA, {"Sponsorship": "Yes, H-1B"}
    )
    assert out == "Kubernetes shop | Sponsorship: Yes, H-1B"


def test_fold_into_empty_notes_has_no_leading_pipe() -> None:
    out = _fold_criteria_values("", _CRITERIA, {"Sponsorship": "Yes"})
    assert out == "Sponsorship: Yes"


def test_fold_skips_unknown_na_and_empty_case_insensitive() -> None:
    out = _fold_criteria_values(
        "notes",
        _CRITERIA,
        {"Sponsorship": "UNKNOWN", "Clearance": "n/a"},
    )
    assert out == "notes"


def test_fold_skips_missing_label() -> None:
    assert _fold_criteria_values("notes", _CRITERIA, {}) == "notes"


def test_fold_collapses_embedded_whitespace() -> None:
    out = _fold_criteria_values("notes", _CRITERIA, {"Sponsorship": "Yes\nH-1B  only"})
    assert out == "notes | Sponsorship: Yes H-1B only"


def test_fold_tolerates_non_str_value() -> None:
    out = _fold_criteria_values("notes", _CRITERIA, {"Sponsorship": ["weird"]})
    assert out == "notes"


def test_fold_skips_empty_and_whitespace_value() -> None:
    """Quiet-by-default: an empty / whitespace-only value adds no dangling label."""
    assert _fold_criteria_values("notes", _CRITERIA, {"Sponsorship": ""}) == "notes"
    assert _fold_criteria_values("notes", _CRITERIA, {"Sponsorship": "   "}) == "notes"


def test_fold_trims_surrounding_whitespace() -> None:
    out = _fold_criteria_values("", _CRITERIA, {"Sponsorship": "  Yes  "})
    assert out == "Sponsorship: Yes"


# --- End-to-end through the worker -----------------------------------------


def test_worker_folds_criteria_into_notes() -> None:
    payload = json.dumps(
        {"fit": 8, "notes": "k8s shop", "criteria": {"Sponsorship": "Yes, H-1B"}}
    )
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.ai_provider.invoke_for",
        return_value=payload,
    ):
        fit, notes, failed = _fetch_fit_notes_for_job(
            _job(), "SRE", "loc", "Van", _CTX, 5, _CRITERIA
        )
    assert not failed
    assert fit == "8/10"
    assert notes == "k8s shop | Sponsorship: Yes, H-1B"


def test_worker_missing_criteria_key_leaves_notes_unchanged() -> None:
    payload = json.dumps({"fit": 7, "notes": "k8s shop"})
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.ai_provider.invoke_for",
        return_value=payload,
    ):
        _fit, notes, failed = _fetch_fit_notes_for_job(
            _job(), "SRE", "loc", "Van", _CTX, 5, _CRITERIA
        )
    assert not failed
    assert notes == "k8s shop"


def test_worker_malformed_criteria_value_does_not_crash() -> None:
    payload = json.dumps({"fit": 7, "notes": "k8s shop", "criteria": "not-a-dict"})
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.ai_provider.invoke_for",
        return_value=payload,
    ):
        _fit, notes, failed = _fetch_fit_notes_for_job(
            _job(), "SRE", "loc", "Van", _CTX, 5, _CRITERIA
        )
    assert not failed
    assert notes == "k8s shop"
