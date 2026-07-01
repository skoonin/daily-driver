"""Tests for the EnrichedJob enrichers (single representation, post-collapse).

The enrichers take and return ``list[EnrichedJob]``; the former dict-based
bodies + typed wrappers were folded into these. These pin the model-level
contract: new frozen instances out, immutable inputs, skipped-status handling.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console as RichConsole

from daily_driver.core.config_models import AIConfig
from daily_driver.core.progress import RunProgress
from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import (
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
    # Budget contract: budget=None -> use the config cap; budget=0 -> no calls.
    # The config cap is zeroed here so the enricher makes no provider calls even
    # when a test leaves budget=None (which falls back to the cap). Without
    # max_enrich_fit=0 the fit enricher makes real claude CLI calls on dev
    # machines where `claude` is on PATH.
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True},
                "enrichment": {"max_enrich_fit": 0},
            }
        )
    )


def test_fit_enrich_returns_enriched_job_list(fake_config: ScrapeContext) -> None:
    """Fit enrich returns a list of EnrichedJob (frozen-model invariant)."""
    j = _enriched()
    out, _stats = enrich_fit_and_notes([j], fake_config, budget=0)
    assert len(out) == 1
    assert isinstance(out[0], EnrichedJob)


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


def test_fit_enriched_counter_not_incremented_on_empty_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job with a pre-existing Fit but blank Notes is eligible; if the model
    returns the same fit and empty notes, nothing is written — and the enriched
    counter must NOT increment (it flows to the manifest's enriched_fit_notes)."""
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
        ai_provider,
        "invoke_for",
        lambda *a, **k: '{"fit": 7, "notes": "", "remote": ""}',
    )
    # fit already set, notes blank -> eligible; model gives no new notes.
    j = _enriched(fit=5, notes="")
    out, stats = enrich_fit_and_notes([j], ctx, budget=5)
    assert out[0].fit == 5  # no cell changed
    assert out[0].notes == ""
    assert stats["enriched"] == 0  # no write -> not counted as enriched


def _fit_ctx(**enrichment: object) -> ScrapeContext:
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


@pytest.mark.parametrize(
    "toggles",
    [
        {"enrich_fit": False, "enrich_notes": True},
        {"enrich_fit": True, "enrich_notes": False},
        {"enrich_fit": False, "enrich_notes": False},
    ],
)
def test_fit_notes_phase_skipped_when_either_toggle_off(
    monkeypatch: pytest.MonkeyPatch, toggles: dict[str, bool]
) -> None:
    """enrich_fit or enrich_notes off => the whole fit/notes wave is skipped.

    The combined call serves both fields, so the wave runs only when BOTH are
    on. With either off, no provider call fires and neither fit nor notes is
    written (they keep their input values).
    """
    ctx = _fit_ctx(**toggles)

    def fake_invoke(*a: object, **k: object) -> str:
        raise AssertionError("no fit/notes call expected when a toggle is off")

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    j = _enriched(description_text="Kubernetes-heavy SRE role", notes="orig")
    out, _stats = enrich_fit_and_notes([j], ctx, budget=5)
    assert out[0].fit is None  # unchanged
    assert out[0].notes == "orig"  # unchanged


def test_fit_notes_phase_runs_when_both_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both toggles on (the default) lets the fit/notes wave write both cells."""
    ctx = _fit_ctx(enrich_fit=True, enrich_notes=True)
    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda *a, **k: '{"fit": 8, "notes": "infra"}'
    )
    j = _enriched(description_text="Platform role")
    out, _stats = enrich_fit_and_notes([j], ctx, budget=5)
    assert out[0].fit == 8
    assert out[0].notes == "infra"


def test_persona_and_home_city_reach_fit_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plugins.job_search.persona + locations.home_city land in the fit prompt.

    Both are surfaced into the natural-language fit/notes prompt; non-default
    values must appear verbatim so the LLM scores against the right persona and
    base location.
    """
    plugin = JobSearchPlugin.model_validate(
        {
            "persona": "Staff Data Engineer",
            "locations": {"home_city": "Berlin, Germany"},
            "enrichment": {
                "provider": "ollama",
                "model": "qwen2.5:14b",
                "max_enrich_fit": 5,
                "enrich_timeout": 5,
            },
        }
    )
    ctx = ScrapeContext(plugin=plugin, ai=AIConfig())
    systems: list[str] = []

    def fake_invoke(prompt, *a, **k):
        # persona + home_city now live in the cached system prompt, not the
        # per-job user prompt.
        systems.append(k.get("system") or "")
        return '{"fit": 6, "notes": "x"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    enrich_fit_and_notes([_enriched(description_text="desc")], ctx, budget=5)

    assert systems
    assert "Staff Data Engineer" in systems[0]
    assert "Berlin, Germany" in systems[0]


def test_system_prompt_is_stable_across_jobs_and_user_carries_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The static block is sent as a byte-identical system prompt for every job
    (so Claude Code prompt-caches it), while the per-job posting rides the user
    prompt. This is the whole point of the split -- a varying system prompt would
    defeat the cache."""
    plugin = JobSearchPlugin.model_validate(
        {
            "persona": "Staff SRE",
            "locations": {"home_city": "Vancouver, BC"},
            "enrichment": {
                "provider": "ollama",
                "model": "qwen2.5:14b",
                "max_enrich_fit": 5,
                "enrich_timeout": 5,
            },
        }
    )
    ctx = ScrapeContext(plugin=plugin, ai=AIConfig())
    calls: list[tuple[str, str]] = []

    def fake_invoke(prompt, *a, **k):
        calls.append((k.get("system") or "", prompt))
        return '{"fit": 6, "notes": "x"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [
        _enriched(company="Acme", url="https://x/1", description_text="d1"),
        _enriched(company="Bravo", url="https://x/2", description_text="d2"),
    ]
    enrich_fit_and_notes(jobs, ctx, budget=5)

    assert len(calls) == 2
    # Identical system prompt across both jobs (cache-stable).
    assert calls[0][0] == calls[1][0]
    assert "Staff SRE" in calls[0][0]
    # Per-job content lives in the user prompt, never in the (cached) system
    # prompt -- checked symmetrically so a per-job leak into the system fails.
    assert "Acme" in calls[0][1] and "Acme" not in calls[0][0]
    assert "Bravo" in calls[1][1] and "Bravo" not in calls[1][0]
    assert calls[0][1] != calls[1][1]


def test_fit_notes_call_sets_safe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The enrichment call passes safe_mode=True: no workspace customizations
    (CLAUDE.md, settings, hooks, MCP, custom commands/agents) load for this
    narrow, structured-output call. Auth/model selection are unaffected --
    safe_mode only adds a CLI flag, it does not change how invoke_for dispatches."""
    plugin = JobSearchPlugin.model_validate(
        {
            "enrichment": {
                "provider": "ollama",
                "model": "qwen2.5:14b",
                "max_enrich_fit": 5,
            }
        }
    )
    ctx = ScrapeContext(plugin=plugin, ai=AIConfig())
    captured: dict[str, object] = {}

    def fake_invoke(prompt, *a, **k):
        captured["safe_mode"] = k.get("safe_mode")
        return '{"fit": 6, "notes": "x"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [_enriched(url="https://x/1", description_text="d1")]
    enrich_fit_and_notes(jobs, ctx, budget=5)

    assert captured["safe_mode"] is True


@pytest.mark.parametrize(
    "provider, expect_warning",
    [("ollama", True), ("claude", False)],
)
def test_large_context_token_warning_is_ollama_only(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
    provider: str,
    expect_warning: bool,
) -> None:
    """The per-call context-token-cost warning fires only on the ollama path.

    On claude the static block (incl. context.md) rides the cached system prompt
    -- sent once per run -- so the per-job multiplication the warning projects no
    longer applies; on ollama it is re-sent every call, so the warning stands."""
    import logging

    big_context = "x " * 4000  # ~2000 tokens, above the 1500-token warn floor
    plugin = JobSearchPlugin.model_validate(
        {"enrichment": {"provider": provider, "model": "m", "enrich_timeout": 5}}
    )
    ctx = ScrapeContext(plugin=plugin, ai=AIConfig(), context_text=big_context)
    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda *a, **k: '{"fit": 6, "notes": "x"}'
    )
    # claude path guards on the CLI being present before it reaches the warning.
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.scraper.enrichment.llm.shutil.which",
        lambda _: "/usr/bin/claude",
    )

    with caplog.at_level(logging.WARNING, logger="daily_driver"):
        enrich_fit_and_notes([_enriched(description_text="d")], ctx, budget=5)

    warned = any("context.md is ~" in r.getMessage() for r in caplog.records)
    assert warned is expect_warning


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


def test_fit_parses_leading_brace_with_trailing_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response that opens with a valid JSON object then adds trailing prose
    must still parse. The span-extraction fallback used to be skipped whenever the
    text already started with a brace, so json.loads choked on the trailing data."""
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
    chatty = '{"fit": 7, "notes": "strong SRE match"} hope that helps'
    monkeypatch.setattr(ai_provider, "invoke_for", lambda *a, **k: chatty)
    j = _enriched(url="https://x/1", description_text="d")
    out, stats = enrich_fit_and_notes([j], ctx)
    assert out[0].fit == 7
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


def test_fit_set_total_rebases_live_phase_to_budget_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live (non-dry) backfill wiring: once the planner resolves, the Fit phase's
    denominator is re-based to the budget-capped plan count (``min(needs, cap)``)
    via ``on_planned=fit_phase.set_total``, while the Detail phase total stays at
    the full row count. Pins the runner wiring so a future refactor cannot
    silently revert the bars to showing the whole active-row count.
    """
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
    jobs = [_enriched(url=f"https://x/{i}", description_text="d") for i in range(5)]

    # Plain (non-TTY) RunProgress: phases hold their denominator in ``_total``
    # without opening a live bar, so the re-base is observable directly.
    console = RichConsole(file=io.StringIO(), force_terminal=False, color_system=None)
    with RunProgress(console, tty=False) as run_progress:
        group = run_progress.group("Job backfill")
        # Both phases are pinned with total=len(jobs) before the plan exists,
        # mirroring _enrich_wave.
        detail_phase = group.phase("Detail pages", total=len(jobs))
        fit_phase = group.phase("Fit and notes", total=len(jobs))
        # _run_llm_enrichment wires the planner's on_planned to fit_phase.set_total.
        enrich_fit_and_notes(jobs, ctx, on_planned=fit_phase.set_total)

    assert fit_phase._total == min(len(jobs), 2)  # re-based to budget-capped count
    assert detail_phase._total == len(jobs)  # full row count, never re-based


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
