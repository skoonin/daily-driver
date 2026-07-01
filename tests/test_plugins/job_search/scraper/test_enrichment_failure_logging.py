"""Diagnostic-improvement tests for enrichment provider-failure logging.

When the underlying provider (claude CLI or ollama) exits non-zero, its
error message is frequently on stdout (not stderr). The warning must
capture both so the cause is visible. Tests patch `claude_cli.invoke`
directly — `ai_provider.invoke_for` translates the resulting
`ClaudeInvocationError` into an `AIInvocationError` whose stdout/stderr
fields preserve the original message.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider, claude_cli
from daily_driver.integrations.ai_provider import AIInvocationError
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import llm as enrichment
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched


def _config() -> ScrapeContext:
    # max_parallel=1 keeps enrichment on the main thread so the warning carries
    # the "[enrich]" tag (not "[enrich wN]"); these tests assert log content,
    # not concurrency — the worker-tag path is covered in test_enrichment_parallel.
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate({"enrichment": {"enrich_timeout": 5}}),
        ai=AIConfig.model_validate({"claude": {"max_parallel": 1}}),
    )


def test_fit_and_notes_warning_includes_stdout(caplog) -> None:
    """Same diagnostic for the fit-and-notes enrichment path."""
    err = claude_cli.ClaudeInvocationError(
        1,
        ["claude", "-p", "..."],
        stdout="Credit balance is too low",
        stderr="",
    )
    jobs = [make_enriched(company="Acme", url="https://example.com/jobs/1")]

    with patch.object(enrichment.shutil, "which", return_value="/usr/bin/claude"):
        with patch.object(enrichment.claude_cli, "invoke", side_effect=err):
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


def test_fit_and_notes_routes_with_plugin_provider() -> None:
    """Site 2 (fit/notes JSON) resolves its route from the plugin config."""
    jobs = [make_enriched(company="Acme", url="https://example.com/j/1")]
    captured: dict = {}
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {"enrichment": {"provider": "ollama", "model": "phi4", "enrich_timeout": 5}}
        ),
        ai=AIConfig.model_validate({"ollama": {"max_parallel": 1}}),
    )

    def fake_invoke(
        prompt, *, provider, model, ai, timeout, system=None, safe_mode=False
    ):
        captured["provider"] = provider
        captured["model"] = model
        return '{"fit": 7, "notes": "kubernetes-heavy"}'

    with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
        out, _ = enrichment.enrich_fit_and_notes(jobs, ctx)

    assert captured["provider"] == "ollama"
    assert captured["model"] == "phi4"
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
    jobs = [make_enriched(company="Acme", url="https://example.com/j/1")]

    with patch.object(enrichment.shutil, "which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=err):
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
    err = claude_cli.ClaudeInvocationError(
        1, ["claude", "-p", "..."], stdout="boom", stderr=""
    )
    jobs = [make_enriched(company="Acme", url="https://example.com/j/1")]

    with patch.object(enrichment.shutil, "which", return_value="/usr/bin/claude"):
        with patch.object(enrichment.claude_cli, "invoke", side_effect=err):
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
# Provider-named timeout warnings + ollama queue hint (PART B.2)
# ---------------------------------------------------------------------------


def test_ollama_timeout_warning_appends_queue_hint_once(caplog) -> None:
    """First ollama timeout in a run logs the full queue hint; later ones don't."""
    from daily_driver.integrations.ai_provider import AITimeoutError

    err = AITimeoutError(
        "ollama timed out after 5s", provider="ollama", timeout_seconds=5
    )
    jobs = [
        make_enriched(company="Acme", url="https://example.com/j/1"),
        make_enriched(company="Beta", url="https://example.com/j/2"),
    ]
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {"enrichment": {"provider": "ollama", "model": "phi4", "enrich_timeout": 5}}
        ),
        ai=AIConfig.model_validate({"ollama": {"max_parallel": 1}}),
    )

    with patch.object(ai_provider, "invoke_for", side_effect=err):
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            enrichment.enrich_fit_and_notes(jobs, ctx)

    # Dedupe identical messages: pytest's caplog can capture a record more than
    # once via the named-logger handler interaction; the real emission is one
    # timeout per job plus one hint (verified separately under -s).
    msgs = {r.getMessage() for r in caplog.records}
    timeout_msgs = {m for m in msgs if "ollama timed out after 5s" in m}
    assert len(timeout_msgs) == 2, f"expected a timeout per job, got: {timeout_msgs}"
    hint_msgs = [m for m in msgs if "OLLAMA_NUM_PARALLEL" in m]
    assert len(hint_msgs) == 1, f"queue hint must log exactly once, got: {hint_msgs}"
    assert "queued requests count against the timeout" in hint_msgs[0]


def test_fit_notes_timeout_uses_full_tag(caplog) -> None:
    """Harmonized: the fit/notes phase uses its full [enrich-fit-notes] tag."""
    from daily_driver.integrations.ai_provider import AITimeoutError

    err = AITimeoutError(
        "claude timed out after 5s", provider="claude", timeout_seconds=5
    )
    jobs = [make_enriched(company="Acme", url="https://example.com/j/1")]

    with patch.object(enrichment.shutil, "which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=err):
            with caplog.at_level(logging.WARNING, logger="daily_driver"):
                enrichment.enrich_fit_and_notes(jobs, _config())

    msgs = [r.getMessage() for r in caplog.records if "timed out" in r.getMessage()]
    assert msgs
    assert all("[enrich-fit-notes]" in m for m in msgs)


def test_serial_fit_pass_arms_queue_hint_once() -> None:
    """A serial fit/notes run arms the once-per-run hint exactly once and leaves
    it latched after the first timeout logs it (so it can't double-emit)."""
    from daily_driver.integrations.ai_provider import AITimeoutError
    from daily_driver.plugins.job_search.scraper.enrichment import llm

    err = AITimeoutError(
        "ollama timed out after 5s", provider="ollama", timeout_seconds=5
    )
    jobs = [
        make_enriched(
            company="Acme",
            url="https://example.com/j/1",
            description_text="infra",
        ),
        make_enriched(
            company="Beta",
            url="https://example.com/j/2",
            description_text="infra",
        ),
    ]
    # max_parallel=1 -> the fit pass takes the serial path.
    ctx = ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {"enrichment": {"provider": "ollama", "model": "phi4", "enrich_timeout": 5}}
        ),
        ai=AIConfig.model_validate({"ollama": {"max_parallel": 1}}),
    )

    reset_calls = [0]
    real_reset = llm._reset_ollama_hint

    def counting_reset() -> None:
        reset_calls[0] += 1
        real_reset()

    with patch.object(llm, "_reset_ollama_hint", counting_reset):
        with patch.object(ai_provider, "invoke_for", side_effect=err):
            enrichment.enrich_fit_and_notes(jobs, ctx)

    # The fit pass arms exactly once across both jobs.
    assert reset_calls[0] == 1, f"expected one re-arm, got {reset_calls[0]}"
    # And the hint stayed latched after the first timeout logged it.
    assert llm._ollama_hint_logged is True
