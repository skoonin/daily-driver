"""Diagnostic-improvement tests for enrichment CalledProcessError logging.

When `claude` CLI exits non-zero, its error message is on stdout (not stderr).
The warning must capture both so the cause is visible.
"""

from __future__ import annotations

import logging
import subprocess
from unittest.mock import patch

from daily_driver.scraper import enrichment


def _config() -> dict:
    return {"job_search": {"scraper": {"enrich_timeout": 5}}}


def test_company_descriptions_warning_includes_stdout(caplog) -> None:
    """When claude exits rc=1 with message on stdout, the warning must include it."""
    err = subprocess.CalledProcessError(
        returncode=1,
        cmd=["claude", "-p", "..."],
        output="Not logged in - Please run /login",
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
    err = subprocess.CalledProcessError(
        returncode=1,
        cmd=["claude", "-p", "..."],
        output="Credit balance is too low",
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
