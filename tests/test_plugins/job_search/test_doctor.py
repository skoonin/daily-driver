"""Tests for daily_driver.plugins.job_search.doctor — backups, Playwright, boards."""

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
# _check_jobs_csv_custom_columns
# ---------------------------------------------------------------------------


def _write_header(path: Path, columns: list[str]) -> None:
    path.write_text(",".join(columns) + "\n", encoding="utf-8")


def test_custom_columns_check_skipped_without_jobs_csv(tmp_path: Path) -> None:
    ws = _FakeWorkspace(output_dir=tmp_path)
    assert doctor._check_jobs_csv_custom_columns(ws) is None


def test_canonical_header_reports_nothing(tmp_path: Path) -> None:
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    _write_header(tmp_path / "jobs.csv", CANONICAL_HEADER)
    ws = _FakeWorkspace(output_dir=tmp_path)
    assert doctor._check_jobs_csv_custom_columns(ws) is None


def test_column_outside_the_canonical_header_is_named(tmp_path: Path) -> None:
    """A column no writer produces survives every rewrite, so say which one."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    _write_header(tmp_path / "jobs.csv", [*CANONICAL_HEADER, "Date Last Seen"])
    ws = _FakeWorkspace(output_dir=tmp_path)

    row = doctor._check_jobs_csv_custom_columns(ws)

    assert row is not None
    assert row.name == "Jobs CSV columns"
    # OK, not WARNING: a column kept on purpose must not raise a row that can
    # never be cleared, and the check cannot tell intent from leftover.
    assert row.status == "OK"
    assert "Date Last Seen" in row.detail
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


def _patch_probe(monkeypatch, *, installed, error=None, seen=None):
    """Stub the browser probe. ``seen`` collects the engines it was asked about."""
    from daily_driver.integrations.playwright import BrowserProbe

    def _probe(engine: str) -> BrowserProbe:
        if seen is not None:
            seen.append(engine)
        return BrowserProbe(installed=installed, playwright_error=error)

    monkeypatch.setattr("daily_driver.integrations.playwright.probe_browser", _probe)


def test_playwright_check_skipped_when_apple_disabled(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    ws = _ws_with_sources(apple=False)
    assert doctor._check_playwright_browser(ws) is None


def test_playwright_check_ok_when_browser_installed(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    _patch_probe(monkeypatch, installed=True)
    ws = _ws_with_sources(apple=True)
    assert doctor._check_playwright_browser(ws) is None


def test_playwright_check_warns_with_fixer_when_missing(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    _patch_probe(monkeypatch, installed=False)
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
    _patch_probe(monkeypatch, installed=False, seen=seen)
    ws = _ws_with_sources(apple=True)
    ws.config.plugins.job_search.scraper = SimpleNamespace(browser="chromium")
    row = doctor._check_playwright_browser(ws)

    assert seen == ["chromium"]
    assert row is not None
    assert "Chromium" in row.detail
    assert "playwright install chromium" in row.fix_hint


def test_playwright_warning_names_the_interpreter_it_probed(monkeypatch):
    """Two interpreters on different playwright versions pin different browser
    builds, so a bare `playwright install` can install into the wrong one and
    leave this warning standing. Both strings must name the probed interpreter."""
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    _patch_probe(monkeypatch, installed=False)
    ws = _ws_with_sources(apple=True)
    ws.config.plugins.job_search.scraper = SimpleNamespace(browser="firefox")

    row = doctor._check_playwright_browser(ws)

    assert row is not None
    assert doctor.sys.executable in row.detail
    assert f"{doctor.sys.executable} -m playwright install firefox" in row.fix_hint


def test_playwright_broken_install_does_not_offer_a_browser_download(monkeypatch):
    """Downloading a browser cannot repair a playwright that failed to answer,
    so this branch offers a repair command and no --fix hook."""
    from daily_driver.integrations.playwright import BrowserProbe

    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(
        "daily_driver.integrations.playwright.probe_browser",
        lambda engine: BrowserProbe(
            installed=False, playwright_error="No module named 'playwright'"
        ),
    )
    ws = _ws_with_sources(apple=True)
    ws.config.plugins.job_search.scraper = SimpleNamespace(browser="firefox")

    row = doctor._check_playwright_browser(ws)

    assert row is not None
    assert row.status == "WARNING"
    assert "No module named 'playwright'" in row.detail
    assert "pip install --force-reinstall playwright" in row.fix_hint
    assert row.plugin_fixer is None


def test_playwright_check_webkit_display_casing(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    _patch_probe(monkeypatch, installed=False)
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


def _ws_with_enrichment_dict(enrichment_data: dict, max_parallel: int = 4):
    """Workspace stand-in built from raw enrichment config (per-phase blocks)."""
    from daily_driver.core.config_models import AIConfig
    from daily_driver.plugins.job_search.config import EnrichmentConfig, JobSearchPlugin

    enrichment = EnrichmentConfig.model_validate(enrichment_data)
    job_search = JobSearchPlugin(enrichment=enrichment, sources={})
    ai = AIConfig.model_validate({"ollama": {"max_parallel": max_parallel}})
    config = SimpleNamespace(ai=ai, plugins=SimpleNamespace(job_search=job_search))
    return SimpleNamespace(config=config, output_dir=Path("/nonexistent-ws/output"))


def test_enrichment_check_flags_phase_override_to_ollama(monkeypatch):
    """A per-phase override routing one pass to an un-pulled ollama model warns.

    Domain stays claude; only fit_notes routes to ollama via its phase block.
    """
    from daily_driver.integrations import ollama_client

    monkeypatch.setattr(
        ollama_client, "list_models", lambda endpoint, timeout=5: ["phi4:latest"]
    )
    ws = _ws_with_enrichment_dict(
        {"fit_notes": {"provider": "ollama", "model": "qwen2.5:14b"}}
    )
    row = doctor._check_enrichment_provider(ws)
    assert row is not None
    assert row.status == "WARNING"
    assert "qwen2.5:14b" in row.detail
    assert "ollama pull qwen2.5:14b" in (row.fix_hint or "")


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


# ── Board sources have boards ────────────────────────────────────────────────


def _ws_with_boards(tmp_path: Path, **sources: list[str] | None) -> SimpleNamespace:
    """Workspace stand-in: each kwarg is an enabled board source with its pins.

    ``None`` marks the source disabled; a list is its pinned board slugs.
    """
    toggles = {}
    for platform, pins in sources.items():
        toggles[platform] = SimpleNamespace(
            enabled=pins is not None, **{f"{platform}_boards": pins or []}
        )
    job_search = SimpleNamespace(sources=toggles)
    return SimpleNamespace(
        config=SimpleNamespace(plugins=SimpleNamespace(job_search=job_search)),
        ephemeral_dir=tmp_path,
    )


def test_boards_check_none_when_no_board_source_enabled(tmp_path: Path) -> None:
    ws = _ws_with_boards(tmp_path, greenhouse=None)
    assert doctor._check_board_sources_have_boards(ws) is None


def test_boards_check_none_when_pinned(tmp_path: Path) -> None:
    ws = _ws_with_boards(tmp_path, greenhouse=["acme"])
    assert doctor._check_board_sources_have_boards(ws) is None


def test_boards_check_none_when_discovery_cache_has_matches(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.scraper.discovery.load_matched_boards",
        lambda _dir, _platform: {"acme": {"matched": 3}},
    )
    ws = _ws_with_boards(tmp_path, greenhouse=[])
    assert doctor._check_board_sources_have_boards(ws) is None


def test_boards_check_warns_when_enabled_with_nothing_to_scrape(
    tmp_path: Path,
) -> None:
    ws = _ws_with_boards(tmp_path, greenhouse=[], ashby=["pinned"], lever=[])
    row = doctor._check_board_sources_have_boards(ws)
    assert row is not None
    assert row.status == "WARNING"
    assert row.name == "Job boards"
    assert "greenhouse, lever" in row.detail
    assert "ashby" not in row.detail
    assert "jobs discover-boards" in row.fix_hint
