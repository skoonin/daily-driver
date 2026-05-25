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

from daily_driver.integrations import ai_provider, claude_cli
from daily_driver.integrations.ai_provider import AIInvocationError
from daily_driver.plugins.job_search.scraper import enrichment


def _config() -> dict:
    return {"job_search": {"scraper": {"enrich_timeout": 5}}}


def test_company_descriptions_warning_includes_stdout(caplog) -> None:
    """When claude exits rc=1 with message on stdout, the warning must include it."""
    err = claude_cli.ClaudeInvocationError(
        1,
        ["claude", "-p", "..."],
        stdout="Not logged in - Please run /login",
        stderr="",
    )
    jobs = [{"company": "Acme", "role": "SRE"}]

    with patch.object(enrichment.shutil, "which", return_value="/usr/bin/claude"):
        with patch.object(enrichment.claude_cli, "invoke", side_effect=err):
            with caplog.at_level(logging.WARNING, logger="daily_driver"):
                enrichment.enrich_company_descriptions(jobs, _config())

    msgs = [r.getMessage() for r in caplog.records if "[enrich]" in r.getMessage()]
    matched = [m for m in msgs if "company=" in m and "rc=1" in m]
    assert matched, f"expected enrich warning, got: {msgs}"
    assert "stdout=" in matched[0]
    assert "Not logged in" in matched[0]
    assert "stderr=" in matched[0]


def test_fit_and_notes_warning_includes_stdout(caplog) -> None:
    """Same diagnostic for the fit-and-notes enrichment path."""
    err = claude_cli.ClaudeInvocationError(
        1,
        ["claude", "-p", "..."],
        stdout="Credit balance is too low",
        stderr="",
    )
    jobs = [{"company": "Acme", "role": "SRE", "url": "https://example.com/jobs/1"}]

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


def test_company_descriptions_routes_to_provider_with_format_false(caplog) -> None:
    """Site 1 (free-text product description) must request format_json=False."""
    jobs = [{"company": "Acme", "role": "SRE"}]
    captured: dict = {}

    def fake_invoke(task, prompt, *, config, timeout, format_json):
        captured["task"] = task
        captured["format_json"] = format_json
        return "Acme makes widgets\n4.1\n"

    with patch.object(enrichment.shutil, "which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            enrichment.enrich_company_descriptions(jobs, _config())

    assert captured["task"] == "enrichment"
    assert captured["format_json"] is False
    assert jobs[0]["product"] == "Acme makes widgets"


def test_fit_and_notes_routes_to_provider_with_format_true(caplog) -> None:
    """Site 2 (fit/notes JSON) must request format_json=True."""
    jobs = [{"company": "Acme", "role": "SRE", "url": "https://example.com/j/1"}]
    captured: dict = {}

    def fake_invoke(task, prompt, *, config, timeout, format_json):
        captured["task"] = task
        captured["format_json"] = format_json
        return '{"fit": 7, "notes": "kubernetes-heavy"}'

    with patch.object(enrichment.shutil, "which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=fake_invoke):
            enrichment.enrich_fit_and_notes(jobs, _config())

    assert captured["task"] == "enrichment"
    assert captured["format_json"] is True
    assert jobs[0]["fit"] == "7/10"


def test_company_descriptions_logs_ai_invocation_error_stdout(caplog) -> None:
    """An ollama-side failure surfaces stdout in the same warning shape."""
    err = AIInvocationError(
        "ollama HTTP 500",
        provider="ollama",
        stdout="server boom",
        stderr="",
        returncode=500,
    )
    jobs = [{"company": "Acme", "role": "SRE"}]

    with patch.object(enrichment.shutil, "which", return_value="/usr/bin/claude"):
        with patch.object(ai_provider, "invoke_for", side_effect=err):
            with caplog.at_level(logging.WARNING, logger="daily_driver"):
                enrichment.enrich_company_descriptions(jobs, _config())

    msgs = [r.getMessage() for r in caplog.records if "[enrich]" in r.getMessage()]
    matched = [m for m in msgs if "company=" in m]
    assert matched, f"expected enrich warning, got: {msgs}"
    assert "stdout=" in matched[0]
    assert "server boom" in matched[0]
