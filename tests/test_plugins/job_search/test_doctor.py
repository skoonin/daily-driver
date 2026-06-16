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
        "daily_driver.integrations.playwright.browser_installed", lambda engine: True
    )
    ws = _ws_with_sources(apple=True)
    assert doctor._check_playwright_browser(ws) is None


def test_playwright_check_warns_with_fixer_when_missing(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(
        "daily_driver.integrations.playwright.browser_installed", lambda engine: False
    )
    ws = _ws_with_sources(apple=True)
    row = doctor._check_playwright_browser(ws)

    assert row is not None
    assert row.name == "Playwright browser"
    assert row.status == "WARNING"
    assert row.plugin_fixer is not None
    assert "apple" in row.detail
    # Defaults to firefox when no browser is configured.
    assert "Firefox" in row.detail


def test_playwright_check_uses_configured_engine(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    seen: list[str] = []

    def _installed(engine: str) -> bool:
        seen.append(engine)
        return False

    monkeypatch.setattr(
        "daily_driver.integrations.playwright.browser_installed", _installed
    )
    ws = _ws_with_sources(apple=True)
    ws.config.plugins.job_search.scraper = SimpleNamespace(browser="chromium")
    row = doctor._check_playwright_browser(ws)

    assert seen == ["chromium"]
    assert row is not None
    assert "Chromium" in row.detail
    assert "playwright install chromium" in row.fix_hint


def test_playwright_check_webkit_display_casing(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(
        "daily_driver.integrations.playwright.browser_installed", lambda engine: False
    )
    ws = _ws_with_sources(apple=True)
    ws.config.plugins.job_search.scraper = SimpleNamespace(browser="webkit")
    row = doctor._check_playwright_browser(ws)

    assert row is not None
    # Canonical casing "WebKit", not .capitalize()'s "Webkit".
    assert "WebKit browser not installed" in row.detail


# ---------------------------------------------------------------------------
# _check_enrichment_provider (enrichment routing moved to the plugin)
# ---------------------------------------------------------------------------


def _ws_with_enrichment(provider: str, model=None, max_parallel: int = 4):
    """Workspace stand-in carrying ai provider blocks + enrichment routing.

    Includes an output_dir pointing at a nonexistent path so the unrelated
    backups / Playwright checks in run_checks short-circuit to no rows.
    """
    from daily_driver.core.config_models import AIConfig
    from daily_driver.plugins.job_search.config import EnrichmentConfig, JobSearchPlugin

    enrichment = EnrichmentConfig.model_validate({"provider": provider, "model": model})
    job_search = JobSearchPlugin(enrichment=enrichment, sources={})
    ai = AIConfig.model_validate({"ollama": {"max_parallel": max_parallel}})
    config = SimpleNamespace(ai=ai, plugins=SimpleNamespace(job_search=job_search))
    return SimpleNamespace(config=config, output_dir=Path("/nonexistent-ws/output"))


def test_enrichment_check_none_when_claude(monkeypatch):
    ws = _ws_with_enrichment("claude")
    assert doctor._check_enrichment_provider(ws) is None


def test_enrichment_check_ok_when_ollama_reachable_with_model(monkeypatch):
    from daily_driver.integrations import ollama_client

    monkeypatch.setattr(
        ollama_client, "list_models", lambda endpoint, timeout=5: ["qwen2.5:14b"]
    )
    ws = _ws_with_enrichment("ollama", model="qwen2.5:14b")
    row = doctor._check_enrichment_provider(ws)
    assert row is not None
    assert row.status == "OK"
    assert "ollama" in row.detail.lower()


def test_enrichment_check_warns_when_unreachable(monkeypatch):
    from daily_driver.integrations import ollama_client

    def _raise(endpoint, timeout=5):
        raise ollama_client.OllamaNotReachableError("not reachable")

    monkeypatch.setattr(ollama_client, "list_models", _raise)
    ws = _ws_with_enrichment("ollama", model="qwen2.5:14b")
    row = doctor._check_enrichment_provider(ws)
    assert row is not None
    assert row.status == "WARNING"
    assert "ollama serve" in (row.fix_hint or "")


def test_enrichment_check_warns_when_model_not_pulled(monkeypatch):
    from daily_driver.integrations import ollama_client

    monkeypatch.setattr(
        ollama_client, "list_models", lambda endpoint, timeout=5: ["phi4:latest"]
    )
    ws = _ws_with_enrichment("ollama", model="qwen2.5:14b")
    row = doctor._check_enrichment_provider(ws)
    assert row is not None
    assert row.status == "WARNING"
    assert "qwen2.5:14b" in row.detail
    assert "ollama pull qwen2.5:14b" in (row.fix_hint or "")


# ---------------------------------------------------------------------------
# NUM_PARALLEL hint row: ollama enrichment + max_parallel > 1
# ---------------------------------------------------------------------------


def test_num_parallel_hint_present_when_ollama_and_parallel_gt_1(monkeypatch):
    from daily_driver.integrations import ollama_client

    monkeypatch.setattr(
        ollama_client, "list_models", lambda endpoint, timeout=5: ["qwen2.5:14b"]
    )
    ws = _ws_with_enrichment("ollama", model="qwen2.5:14b", max_parallel=4)
    rows = doctor.run_checks(ws)
    hints = [r for r in rows if r.name == "Ollama NUM_PARALLEL"]
    assert len(hints) == 1
    hint = hints[0]
    assert "launchctl setenv OLLAMA_NUM_PARALLEL 4" in hint.detail + (
        hint.fix_hint or ""
    )
    assert "OLLAMA_NUM_PARALLEL=4 ollama serve" in hint.detail + (hint.fix_hint or "")
    assert "systemctl edit ollama.service" in hint.detail + (hint.fix_hint or "")


def test_num_parallel_hint_absent_when_parallel_is_1(monkeypatch):
    from daily_driver.integrations import ollama_client

    monkeypatch.setattr(
        ollama_client, "list_models", lambda endpoint, timeout=5: ["qwen2.5:14b"]
    )
    ws = _ws_with_enrichment("ollama", model="qwen2.5:14b", max_parallel=1)
    rows = doctor.run_checks(ws)
    assert not [r for r in rows if r.name == "Ollama NUM_PARALLEL"]


def test_num_parallel_hint_absent_when_claude(monkeypatch):
    ws = _ws_with_enrichment("claude", max_parallel=4)
    rows = doctor.run_checks(ws)
    assert not [r for r in rows if r.name == "Ollama NUM_PARALLEL"]
