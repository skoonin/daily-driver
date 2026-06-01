"""Smoke tests for the scraper package.

These tests verify that the scraper submodules load cleanly and that
the public entry points (``run``, ``run_backfill``, ``load_config_file``)
are wired correctly. Source-level behavior (HTTP, HTML parsing, playwright)
is out of scope here — see test_scraper_run.py for CLI-level integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_package_exports_public_api() -> None:
    import daily_driver.plugins.job_search.scraper as scraper

    assert hasattr(scraper, "run")
    assert hasattr(scraper, "run_backfill")
    assert hasattr(scraper, "load_config_file")


def test_runner_and_csv_io_load_without_side_effects() -> None:
    """The runner and csv_io modules must import without kicking off I/O."""
    from daily_driver.plugins.job_search.scraper import csv_io, runner

    assert callable(runner.run_all_scrapers)
    assert callable(csv_io.load_existing_jobs)
    assert hasattr(csv_io, "CANONICAL_HEADER")
    assert isinstance(csv_io.CANONICAL_HEADER, list)


def test_load_config_file_reads_yaml(tmp_path: Path) -> None:
    from daily_driver.plugins.job_search.scraper import load_config_file

    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text("output_dir: /tmp/x\njob_search:\n  roles: [SRE]\n")

    cfg = load_config_file(cfg_path)

    assert cfg["output_dir"] == "/tmp/x"
    assert cfg["job_search"]["roles"] == ["SRE"]


def test_run_returns_zero_when_scraper_disabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Disabled scraper prints a hint and returns 0 without touching network."""
    from daily_driver.plugins.job_search.config import JobSearchPlugin
    from daily_driver.plugins.job_search.scraper import run

    plugin = JobSearchPlugin.model_validate({"scraper": {"enabled": False}})

    rc = run(plugin, tmp_path)

    assert rc == 0
    captured = capsys.readouterr()
    assert "Scraper disabled" in captured.err
