"""Smoke tests for the scraper package.

These tests verify that the scraper submodules load cleanly and that
the public entry points (``run``, ``run_backfill``) are wired correctly.
Source-level behavior (HTTP, HTML parsing, playwright) is out of scope
here — see test_scraper_run.py for CLI-level integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_package_exports_public_api() -> None:
    import daily_driver.plugins.job_search.scraper as scraper

    assert hasattr(scraper, "run")
    assert hasattr(scraper, "run_backfill")


def test_runner_and_csv_io_load_without_side_effects() -> None:
    """The runner and csv_io modules must import without kicking off I/O."""
    from daily_driver.plugins.job_search.scraper import csv_io, runner

    assert callable(runner.run_all_scrapers)
    assert callable(csv_io.load_existing_jobs)
    assert hasattr(csv_io, "CANONICAL_HEADER")
    assert isinstance(csv_io.CANONICAL_HEADER, list)


def test_run_without_sites_says_so_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no site listed, a run names the setting and returns 0."""
    from daily_driver.plugins.job_search.config import JobSearchPlugin
    from daily_driver.plugins.job_search.scraper import run

    plugin = JobSearchPlugin.model_validate({})

    rc = run(plugin, tmp_path, tmp_path)

    assert rc == 0
    captured = capsys.readouterr()
    assert "No sites to search" in captured.err


def test_run_with_sites_from_the_command_line_does_not_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--sources` picks the sites, so an empty `sources` block is fine."""
    from daily_driver.plugins.job_search.config import JobSearchPlugin
    from daily_driver.plugins.job_search.scraper import run

    rc = run(
        JobSearchPlugin.model_validate({}), tmp_path, tmp_path, sources_override=[]
    )

    assert rc == 0
    assert "No sites to search" not in capsys.readouterr().err


def test_run_with_every_listed_site_off_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.plugins.job_search.config import JobSearchPlugin
    from daily_driver.plugins.job_search.scraper import run

    plugin = JobSearchPlugin.model_validate(
        {"sources": {"hn_jobs": {"enabled": False}}}
    )

    assert run(plugin, tmp_path, tmp_path) == 0
    assert "No sites to search" in capsys.readouterr().err
