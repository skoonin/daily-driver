"""Tests for daily_driver.plugins.job_search.doctor — backups + Playwright."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from daily_driver.plugins.job_search import doctor


@dataclass
class _FakeWorkspace:
    output_dir: Path


def _ws_with_sources(**toggles: bool) -> SimpleNamespace:
    """Workspace stand-in carrying a job_search.sources toggle map.

    Each kwarg becomes a source toggle with the given enabled flag, mirroring
    `workspace.config.plugins.job_search.sources` at runtime.
    """
    sources = {k: SimpleNamespace(enabled=v) for k, v in toggles.items()}
    job_search = SimpleNamespace(sources=sources)
    return SimpleNamespace(
        config=SimpleNamespace(plugins=SimpleNamespace(job_search=job_search))
    )


def _make_baks(backups_dir: Path, count: int) -> None:
    backups_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (backups_dir / f"jobs.csv.bak.2026-05-{i:02d}").write_text(
            "x", encoding="utf-8"
        )


def test_no_backups_dir_returns_empty(tmp_path: Path) -> None:
    ws = _FakeWorkspace(output_dir=tmp_path)
    assert doctor.run_checks(ws) == []


def test_few_backups_below_threshold_no_row(tmp_path: Path) -> None:
    _make_baks(tmp_path / "backups", 5)
    ws = _FakeWorkspace(output_dir=tmp_path)
    assert doctor.run_checks(ws) == []


def test_accumulated_backups_warns(tmp_path: Path) -> None:
    _make_baks(tmp_path / "backups", 8)
    ws = _FakeWorkspace(output_dir=tmp_path)
    results = doctor.run_checks(ws)
    assert len(results) == 1
    row = results[0]
    assert row.name == "Jobs backups"
    assert row.status == "WARNING"
    assert row.plugin_fixer is None


# ---------------------------------------------------------------------------
# _check_playwright_browser
# ---------------------------------------------------------------------------


def test_playwright_check_skipped_off_macos(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    ws = _ws_with_sources(apple=True)
    assert doctor._check_playwright_browser(ws) is None


def test_playwright_check_skipped_when_no_playwright_source(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    # remoteok is not a Playwright source; apple absent => off.
    ws = _ws_with_sources(remoteok=True)
    assert doctor._check_playwright_browser(ws) is None


def test_playwright_check_skipped_when_apple_disabled(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    ws = _ws_with_sources(apple=False)
    assert doctor._check_playwright_browser(ws) is None


def test_playwright_check_ok_when_browser_installed(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(
        "daily_driver.integrations.playwright.firefox_installed", lambda: True
    )
    ws = _ws_with_sources(apple=True)
    assert doctor._check_playwright_browser(ws) is None


def test_playwright_check_warns_with_fixer_when_missing(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(
        "daily_driver.integrations.playwright.firefox_installed", lambda: False
    )
    from daily_driver.integrations import playwright as pw

    ws = _ws_with_sources(apple=True)
    row = doctor._check_playwright_browser(ws)

    assert row is not None
    assert row.name == "Playwright browser"
    assert row.status == "WARNING"
    assert row.plugin_fixer is pw.install_firefox
    assert "apple" in row.detail
