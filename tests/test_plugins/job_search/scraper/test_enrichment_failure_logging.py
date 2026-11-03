"""Diagnostic-improvement tests for enrichment provider-failure logging.

When the underlying provider (a command such as claude, or an HTTP server such
as ollama) fails, its error message is frequently on stdout (not stderr). The
warning must capture both so the cause is visible. Tests make the provider
layer raise `AIInvocationError`, the one failure type every provider
normalizes to, with stdout/stderr carrying the original message.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from daily_driver.integrations import provider as ai
from daily_driver.integrations.provider import AIInvocationError, AITimeoutError
from daily_driver.plugins.job_search import FIT_NOTES
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import llm as enrichment
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import (
    claude_provider,
    make_enriched,
    ollama_provider,
)


def _config() -> ScrapeContext:
    # max_parallel=1 keeps enrichment on the main thread so the warning carries
    # the "[enrich]" tag (not "[enrich wN]"); these tests assert log content,
    # not concurrency — the worker-tag path is covered in test_enrichment_parallel.
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate({"enrichment": {"enrich_timeout": 5}}),
        provider=claude_provider(max_parallel=1),
    )


def test_fit_and_notes_warning_includes_stdout(caplog) -> None:
    """Same diagnostic for the fit-and-notes enrichment path."""
    err = AIInvocationError(
        "claude exited 1",
        provider="claude",
        stdout="Credit balance is too low",
        stderr="",
        returncode=1,
    )
    jobs = [
        make_enriched(
            company="Acme", url="https://example.com/jobs/1", description_text="infra"
        )
    ]

    with patch.object(ai, "invoke", side_effect=err):
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            enrichment.enrich_fit_and_notes(jobs, _config())

    msgs = [
        r.getMessage() for r in caplog.records if "[enrich-fit-notes]" in r.getMessage()
    ]
    matched = [m for m in msgs if "company=" in m and "rc=1" in m]
    assert matched, f"expected enrich-fit-notes warning, got: {msgs}"
    assert "stdout=" in matched[0]
    assert "Credit balance is too low" in matched[0]
    assert "stderr=" in matched[0]


def test_fit_and_notes_runs_on_the_context_provider() -> None:
    """Site 2 (fit/notes JSON) runs the fit_notes task on the context's provider."""
    jobs = [
        make_enriched(
            company="Acme", url="https://example.com/j/1", description_text="infra"
        )
    ]
    captured: dict = {}
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate({"enrichment": {"enrich_timeout": 5}}),
        provider=ollama_provider(model="phi4", max_parallel=1),
    )

    def fake_invoke(provider, task, prompt, *, system=None, timeout=None):
        captured["provider"] = provider
        captured["task"] = task
        captured["timeout"] = timeout
        return '{"fit": 7, "notes": "kubernetes-heavy"}'

    with patch.object(ai, "invoke", side_effect=fake_invoke):
        out, _ = enrichment.enrich_fit_and_notes(jobs, ctx)

    assert captured["provider"] is ctx.provider
    assert captured["provider"].name == "ollama"
    assert captured["provider"].settings.model == "phi4"
    assert captured["task"] is FIT_NOTES
    assert captured["timeout"] == 5
    assert out[0].fit == 7


def test_fit_notes_logs_ai_invocation_error_stdout(caplog) -> None:
    """An ollama-side failure surfaces stdout in the fit/notes warning shape."""
    err = AIInvocationError(
        "ollama HTTP 500",
        provider="ollama",
        stdout="server boom",
        stderr="",
        returncode=500,
    )
    jobs = [
        make_enriched(
            company="Acme", url="https://example.com/j/1", description_text="infra"
        )
    ]

    with patch.object(ai, "invoke", side_effect=err):
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            enrichment.enrich_fit_and_notes(jobs, _config())

    msgs = [
        r.getMessage() for r in caplog.records if "[enrich-fit-notes]" in r.getMessage()
    ]
    matched = [m for m in msgs if "company=" in m]
    assert matched, f"expected enrich-fit-notes warning, got: {msgs}"
    assert "stdout=" in matched[0]
    assert "server boom" in matched[0]


def test_fit_notes_failed_job_logs_info_one_liner(caplog) -> None:
    """A failed fit/notes call emits a per-job INFO one-liner (visible at -v),
    so a single failure is identifiable without dropping to -vv."""
    err = AIInvocationError(
        "claude exited 1", provider="claude", stdout="boom", stderr="", returncode=1
    )
    jobs = [
        make_enriched(
            company="Acme", url="https://example.com/j/1", description_text="infra"
        )
    ]

    with patch.object(ai, "invoke", side_effect=err):
        with caplog.at_level(logging.INFO, logger="daily_driver"):
            enrichment.enrich_fit_and_notes(jobs, _config())

    info_msgs = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO and "Acme" in r.getMessage()
    ]
    assert any(
        "enrichment failed" in m for m in info_msgs
    ), f"expected an INFO per-job failure line, got: {info_msgs}"


# ---------------------------------------------------------------------------
# Provider-named timeout warnings
# ---------------------------------------------------------------------------


def test_timeout_warning_names_provider_and_seconds_per_job(caplog) -> None:
    """Each timed-out job logs its own warning naming the provider and limit."""
    err = AITimeoutError(
        "ollama timed out after 5s", provider="ollama", timeout_seconds=5
    )
    jobs = [
        make_enriched(
            company="Acme", url="https://example.com/j/1", description_text="infra"
        ),
        make_enriched(
            company="Beta", url="https://example.com/j/2", description_text="infra"
        ),
    ]
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate({"enrichment": {"enrich_timeout": 5}}),
        provider=ollama_provider(model="phi4", max_parallel=1),
    )

    with patch.object(ai, "invoke", side_effect=err):
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            enrichment.enrich_fit_and_notes(jobs, ctx)

    # Dedupe identical messages: pytest's caplog can capture a record more than
    # once via the named-logger handler interaction.
    msgs = {r.getMessage() for r in caplog.records}
    timeout_msgs = {m for m in msgs if "ollama timed out after 5s" in m}
    assert len(timeout_msgs) == 2, f"expected a timeout per job, got: {timeout_msgs}"
    assert any("Acme" in m for m in timeout_msgs)
    assert any("Beta" in m for m in timeout_msgs)


def test_fit_notes_timeout_uses_full_tag(caplog) -> None:
    """Harmonized: the fit/notes phase uses its full [enrich-fit-notes] tag."""
    err = AITimeoutError(
        "claude timed out after 5s", provider="claude", timeout_seconds=5
    )
    jobs = [
        make_enriched(
            company="Acme", url="https://example.com/j/1", description_text="infra"
        )
    ]

    with patch.object(ai, "invoke", side_effect=err):
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            enrichment.enrich_fit_and_notes(jobs, _config())

    msgs = [r.getMessage() for r in caplog.records if "timed out" in r.getMessage()]
    assert msgs
    assert all("[enrich-fit-notes]" in m for m in msgs)
