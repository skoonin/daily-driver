"""LinkedIn description backfill: job-id extraction, parser, anti-drift, and the
fill-missing-only / graceful-degradation behavior of ``fetch_linkedin_descriptions``.

All HTTP is mocked at the ``enrichment.linkedin._api_get`` seam (the same seam
``test_enrichment_skip`` patches) -- no test touches the network.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment.linkedin import (
    _linkedin_job_id,
    fetch_linkedin_descriptions,
    render_linkedin_summary,
)
from daily_driver.plugins.job_search.scraper.enrichment.llm import (
    _build_fit_plan,
    enrich_fit_and_notes,
    fit_notes_target_indices,
)
from daily_driver.plugins.job_search.scraper.models import EnrichedJob
from daily_driver.plugins.job_search.scraper.parsing import parse_linkedin_description
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched

_LINKEDIN_PATCH = "daily_driver.plugins.job_search.scraper.enrichment.linkedin._api_get"


@pytest.fixture
def ctx_on() -> ScrapeContext:
    """A context with no throttle delay, so fetches resolve synchronously in tests."""
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
                "enrichment": {"detail_delay_seconds": 0},
            }
        )
    )


def _li_job(job_id: str, **overrides: Any) -> EnrichedJob:
    return make_enriched(
        url=f"https://www.linkedin.com/jobs/view/{job_id}",
        source="linkedin",
        **overrides,
    )


def _resp(text: str, url: str) -> MagicMock:
    """A mocked HTTP response carrying ``.text`` and a (final) ``.url``."""
    resp = MagicMock()
    resp.text = text
    resp.url = url
    return resp


_MARKUP_HTML = (
    '<html><body><div class="show-more-less-html__markup">'
    "We run Kubernetes at scale. Terraform, Datadog, on-call rotation."
    "</div></body></html>"
)


# --- Job-id extraction ------------------------------------------------------


class TestJobIdExtraction:
    def test_numeric_id_from_view_url(self) -> None:
        assert _linkedin_job_id("https://www.linkedin.com/jobs/view/4012345678") == (
            "4012345678"
        )

    def test_id_extracted_with_trailing_query(self) -> None:
        url = "https://www.linkedin.com/jobs/view/123?refId=abc&trk=xyz"
        assert _linkedin_job_id(url) == "123"

    def test_none_for_non_linkedin_host(self) -> None:
        assert _linkedin_job_id("https://example.com/jobs/view/123") is None

    def test_none_for_linkedin_without_view_id(self) -> None:
        assert _linkedin_job_id(
            "https://www.linkedin.com/jobs/search/?keywords=sre"
        ) is (None)

    def test_none_for_empty(self) -> None:
        assert _linkedin_job_id("") is None


# --- Parser -----------------------------------------------------------------


class TestParser:
    def test_extracts_markup_text(self) -> None:
        out = parse_linkedin_description(_MARKUP_HTML)
        assert out["description_text"].startswith("We run Kubernetes")
        assert "Terraform" in out["description_text"]

    def test_signup_wall_yields_empty(self) -> None:
        html = "<html><body><div>Join LinkedIn to see this job</div></body></html>"
        assert parse_linkedin_description(html) == {}

    def test_empty_markup_div_yields_empty(self) -> None:
        html = '<div class="show-more-less-html__markup">   </div>'
        assert parse_linkedin_description(html) == {}

    def test_empty_string_yields_empty(self) -> None:
        assert parse_linkedin_description("") == {}

    def test_whitespace_only_yields_empty(self) -> None:
        assert parse_linkedin_description("   \n  ") == {}


# --- Anti-drift: shared selection -------------------------------------------


class TestAntiDrift:
    """``fit_notes_target_indices`` must select exactly the rows ``_build_fit_plan``
    enriches, or the LinkedIn fetch and the fit/notes pass would drift apart."""

    def _mixed_jobs(self) -> list[EnrichedJob]:
        return [
            _li_job("1"),  # eligible
            make_enriched(url="https://x.com/a", fit=8, notes="done"),  # ineligible
            _li_job("2"),  # eligible
            make_enriched(url="https://x.com/excluded"),  # eligible but excluded
            _li_job("3"),  # eligible
        ]

    def test_matches_build_fit_plan_selection(self, ctx_on: ScrapeContext) -> None:
        jobs = self._mixed_jobs()
        exclude = frozenset({"https://x.com/excluded"})
        plan = _build_fit_plan(
            jobs, ctx_on, budget=None, progress=None, exclude_urls=exclude
        )
        assert plan is not None
        helper = fit_notes_target_indices(jobs, None, ctx_on.plugin.enrichment, exclude)
        assert helper == plan.target_idx
        # Input order preserved; ineligible (idx 1) and excluded (idx 3) dropped.
        assert helper == [0, 2, 4]

    def test_budget_zero_selects_nothing(self, ctx_on: ScrapeContext) -> None:
        jobs = self._mixed_jobs()
        plan = _build_fit_plan(jobs, ctx_on, budget=0, progress=None)
        assert plan is not None
        helper = fit_notes_target_indices(jobs, 0, ctx_on.plugin.enrichment)
        assert helper == plan.target_idx == []

    def test_cap_honored(self, ctx_on: ScrapeContext) -> None:
        jobs = self._mixed_jobs()
        plan = _build_fit_plan(jobs, ctx_on, budget=2, progress=None)
        assert plan is not None
        helper = fit_notes_target_indices(jobs, 2, ctx_on.plugin.enrichment)
        assert helper == plan.target_idx == [0, 2]


# --- fetch_linkedin_descriptions behavior -----------------------------------


class TestFetchBehavior:
    def test_fills_missing_description(self, ctx_on: ScrapeContext) -> None:
        jobs = [_li_job("42")]
        resp = _resp(_MARKUP_HTML, "https://www.linkedin.com/jobs/view/42")
        with patch(_LINKEDIN_PATCH, return_value=resp) as api_get:
            out, stats = fetch_linkedin_descriptions(jobs, ctx_on, [0])
        assert api_get.call_count == 1
        # Canonicalized to the view URL with the extracted id.
        assert api_get.call_args.args[1] == "https://www.linkedin.com/jobs/view/42"
        assert out[0].description_text.startswith("We run Kubernetes")
        assert stats["filled"] == 1
        assert stats["fetched"] == 1

    def test_fill_missing_only_skips_existing_description(
        self, ctx_on: ScrapeContext
    ) -> None:
        jobs = [_li_job("42", description_text="Already populated.")]
        with patch(_LINKEDIN_PATCH) as api_get:
            _out, stats = fetch_linkedin_descriptions(jobs, ctx_on, [0])
        assert api_get.call_count == 0
        assert stats["skipped"] == 1
        assert stats["fetched"] == 0

    def test_non_linkedin_row_in_target_is_skipped(self, ctx_on: ScrapeContext) -> None:
        jobs = [make_enriched(url="https://boards.greenhouse.io/acme/jobs/1")]
        with patch(_LINKEDIN_PATCH) as api_get:
            _out, stats = fetch_linkedin_descriptions(jobs, ctx_on, [0])
        assert api_get.call_count == 0
        assert stats["skipped"] == 1

    def test_signup_wall_leaves_row_untouched(self, ctx_on: ScrapeContext) -> None:
        jobs = [_li_job("42")]
        walled = _resp("<html></html>", "https://www.linkedin.com/signup/cold-join")
        with patch(_LINKEDIN_PATCH, return_value=walled):
            out, stats = fetch_linkedin_descriptions(jobs, ctx_on, [0])
        assert out[0].description_text == ""
        assert stats["signup_walled"] == 1
        assert stats["filled"] == 0

    def test_none_response_leaves_row_untouched(self, ctx_on: ScrapeContext) -> None:
        jobs = [_li_job("42")]
        with patch(_LINKEDIN_PATCH, return_value=None):
            out, stats = fetch_linkedin_descriptions(jobs, ctx_on, [0])
        assert out[0].description_text == ""
        assert stats["signup_walled"] == 1

    def test_stops_after_consecutive_failures(self, ctx_on: ScrapeContext) -> None:
        # Eight LinkedIn rows, every fetch fails -> stop after the 5th, leaving
        # the remaining three un-fetched.
        jobs = [_li_job(str(i)) for i in range(8)]
        with patch(_LINKEDIN_PATCH, return_value=None) as api_get:
            _out, stats = fetch_linkedin_descriptions(jobs, ctx_on, list(range(8)))
        assert api_get.call_count == 5
        assert stats["stopped_early"] is True
        assert stats["signup_walled"] == 5

    def test_success_resets_failure_counter(self, ctx_on: ScrapeContext) -> None:
        # Fail, fail, succeed, fail, fail, fail, fail -> the success resets the
        # run, so the streak never reaches 5 and the phase does not stop early.
        jobs = [_li_job(str(i)) for i in range(7)]
        ok = _resp(_MARKUP_HTML, "https://www.linkedin.com/jobs/view/2")
        responses = [None, None, ok, None, None, None, None]
        with patch(_LINKEDIN_PATCH, side_effect=responses) as api_get:
            _out, stats = fetch_linkedin_descriptions(jobs, ctx_on, list(range(7)))
        assert api_get.call_count == 7
        assert stats["stopped_early"] is False
        assert stats["filled"] == 1

    def test_comp_preserved_on_fetched_row(self, ctx_on: ScrapeContext) -> None:
        """Filling a description must not disturb an existing comp value."""
        jobs = [_li_job("42", comp="$200k")]
        resp = _resp(_MARKUP_HTML, "https://www.linkedin.com/jobs/view/42")
        with patch(_LINKEDIN_PATCH, return_value=resp):
            out, _ = fetch_linkedin_descriptions(jobs, ctx_on, [0])
        assert out[0].comp == "$200k"
        assert out[0].description_text.startswith("We run Kubernetes")


# --- Integration: fetch then fit/notes --------------------------------------


def test_fetched_description_feeds_fit_notes(ctx_on: ScrapeContext) -> None:
    """A LinkedIn row with no description gets one filled, then the fit/notes pass
    (mocked LLM) writes Notes for it."""
    jobs = [_li_job("42")]
    target_idx = fit_notes_target_indices(jobs, None, ctx_on.plugin.enrichment)
    resp = _resp(_MARKUP_HTML, "https://www.linkedin.com/jobs/view/42")
    with patch(_LINKEDIN_PATCH, return_value=resp):
        jobs, li_stats = fetch_linkedin_descriptions(jobs, ctx_on, target_idx)
    assert li_stats["filled"] == 1
    assert jobs[0].description_text

    payload = json.dumps({"fit": 8, "notes": "Kubernetes platform team"})
    with patch(
        "daily_driver.plugins.job_search.scraper.enrichment.llm.ai_provider.invoke_for",
        return_value=payload,
    ):
        out, fn_stats = enrich_fit_and_notes(jobs, ctx_on)
    assert fn_stats["enriched"] == 1
    assert out[0].notes == "Kubernetes platform team"
    assert out[0].fit == 8


# --- Summary rendering -------------------------------------------------------


def test_render_summary_plain() -> None:
    stats = {"filled": 3, "skipped": 12, "signup_walled": 2, "stopped_early": False}
    assert render_linkedin_summary(stats) == "3 filled, 12 skipped, 2 walled"


def test_render_summary_stopped_early() -> None:
    stats = {"filled": 0, "skipped": 1, "signup_walled": 5, "stopped_early": True}
    assert (
        render_linkedin_summary(stats)
        == "0 filled, 1 skipped, 5 walled (stopped early)"
    )
