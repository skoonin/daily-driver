"""Job-search doctor checks contributed to the core doctor run.

Core doctor imports ``run_checks`` lazily (via the plugin's ``doctor_checks``
dotted path), so this module is never loaded for a doctor run that does not
exercise plugin health checks.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daily_driver.core.doctor import CheckResult
    from daily_driver.core.workspace import Workspace


def _check_jobs_backups(workspace: Workspace) -> CheckResult | None:
    """Warn if `jobs.csv.bak.*` snapshots have accumulated under backups/.

    Each backfill / migration run drops a `.bak.<utc-stamp>` snapshot before
    mutating jobs.csv. Users won't notice them, so over months of use they
    pile up.
    """
    from daily_driver.core.doctor import CheckResult

    backups_dir = workspace.output_dir / "backups"
    if not backups_dir.exists():
        return None
    baks = sorted(backups_dir.glob("jobs.csv.bak.*"))
    if len(baks) <= 5:
        return None
    keep = baks[-3:]
    drop = baks[:-3]
    drop_names = ", ".join(p.name for p in drop[:3]) + (
        f", … (+{len(drop) - 3} more)" if len(drop) > 3 else ""
    )
    return CheckResult(
        name="Jobs backups",
        status="WARNING",
        detail=(
            f"{len(baks)} jobs.csv.bak.* files in {backups_dir}; "
            f"keeping {len(keep)} most recent is plenty. Old: {drop_names}"
        ),
        fix_hint=(
            f"Delete old snapshots: rm {backups_dir}/jobs.csv.bak.* "
            f"(then re-create the most recent if you want a rollback point)"
        ),
    )


def _enabled_playwright_sources(workspace: Workspace) -> list[str]:
    """Names of configured-on sources that require the Playwright browser.

    A source counts as enabled only when explicitly toggled on, matching the
    runtime gate in ``scrape_all.run_all_scrapers`` (an absent toggle is off).
    """
    from daily_driver.plugins.job_search.scraper.scrape_all import _PLAYWRIGHT_SOURCES

    config = getattr(workspace, "config", None)
    job_cfg = getattr(getattr(config, "plugins", None), "job_search", None)
    if job_cfg is None:
        return []
    toggles = job_cfg.sources
    return [
        sid
        for sid in _PLAYWRIGHT_SOURCES
        if (toggle := toggles.get(sid)) is not None and toggle.enabled
    ]


# Canonical display casing per engine; .capitalize() would render "Webkit".
_ENGINE_DISPLAY = {"firefox": "Firefox", "chromium": "Chromium", "webkit": "WebKit"}


def _configured_browser(workspace: Workspace) -> str:
    """Return the configured Playwright engine, defaulting to firefox.

    Only called after ``_enabled_playwright_sources`` confirms a Playwright
    source is on, which already proves ``config.plugins`` resolves. ``job_search``
    is a dynamic plugin attribute (not declared on ``PluginsConfig``), and test
    stubs may omit ``scraper`` — hence the two ``getattr`` guards.
    """
    from daily_driver.integrations import playwright as pw

    job_cfg = getattr(workspace.config.plugins, "job_search", None)
    scraper = getattr(job_cfg, "scraper", None)
    return getattr(scraper, "browser", None) or pw.DEFAULT_ENGINE


def _check_playwright_browser(workspace: Workspace) -> CheckResult | None:
    """Warn when a Playwright source is enabled but its browser build is missing.

    The ``playwright`` pip package installs without the browser binaries
    (wheels run no install-time code), so an enabled Apple source dies at
    launch. Checks the configured engine
    (``plugins.job_search.scraper.browser``). Gated to macOS — the only
    platform the launchers target — and emitted only when such a source is
    actually configured on, so users who do not run Playwright sources never
    see browser noise.
    """
    from daily_driver.core.doctor import CheckResult
    from daily_driver.integrations import playwright as pw

    if sys.platform != "darwin":
        return None
    enabled = _enabled_playwright_sources(workspace)
    if not enabled:
        return None
    engine = _configured_browser(workspace)
    if pw.browser_installed(engine):
        return None
    return CheckResult(
        name="Playwright browser",
        status="WARNING",
        detail=(
            f"{_ENGINE_DISPLAY.get(engine, engine.capitalize())} browser not "
            f"installed; source(s) {', '.join(enabled)} will fail at launch"
        ),
        fix_hint=f"Run: daily-driver doctor --fix (or: playwright install {engine})",
        plugin_fixer=lambda: pw.install_browser(engine),
    )


def _enrichment_cfg(workspace: Workspace):  # type: ignore[no-untyped-def]
    """Resolve the job_search enrichment config, or None if unavailable.

    Test stubs and configs without the plugin block may omit any link in the
    chain, so each hop is guarded rather than assumed.
    """
    config = getattr(workspace, "config", None)
    job_cfg = getattr(getattr(config, "plugins", None), "job_search", None)
    return getattr(job_cfg, "enrichment", None)


def _check_enrichment_provider(workspace: Workspace) -> CheckResult | None:
    """Verify Ollama reachability + model presence when enrichment routes to it.

    Enrichment routing lives in `plugins.job_search.enrichment` (a domain
    default plus a per-phase `fit_notes` override); the shared connection block
    (`ai.ollama.endpoint`) is core infra. The phase is resolved through the
    routing chain; returns None when it does not resolve to ollama. WARNING on
    failure, matching the core drift convention.
    """
    from daily_driver.core.doctor import CheckResult
    from daily_driver.integrations import ai_provider

    enrichment = _enrichment_cfg(workspace)
    if enrichment is None:
        return None
    ai_cfg = getattr(getattr(workspace, "config", None), "ai", None)
    if ai_cfg is None:
        return None

    from daily_driver.integrations import ollama_client
    from daily_driver.integrations.ollama_client import OllamaNotReachableError

    ollama_phases: list[tuple[str, str]] = []
    provider, model = ai_provider.resolve_route(
        ai_cfg, task="fit_notes", domain_cfg=enrichment
    )
    if provider == "ollama":
        ollama_phases.append(("fit_notes", model or ollama_client.DEFAULT_MODEL))
    if not ollama_phases:
        return None

    endpoint = ai_cfg.ollama.endpoint
    routed = ", ".join(f"{phase}: ollama {model}" for phase, model in ollama_phases)

    try:
        pulled = ollama_client.list_models(endpoint, timeout=5)
    except OllamaNotReachableError:
        return CheckResult(
            name="Enrichment provider",
            status="WARNING",
            detail=f"ollama at {endpoint} not reachable (enrichment: {routed})",
            fix_hint="Start the server: ollama serve",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="Enrichment provider",
            status="WARNING",
            detail=f"ollama at {endpoint} returned an error: {exc}",
            fix_hint="Verify `ollama serve` is healthy on the configured endpoint.",
        )

    missing = [(phase, model) for phase, model in ollama_phases if model not in pulled]
    if missing:
        miss_models = ", ".join(sorted({model for _, model in missing}))
        first_missing = missing[0][1]
        return CheckResult(
            name="Enrichment provider",
            status="WARNING",
            detail=f"ollama reachable at {endpoint}; model(s) not pulled: {miss_models}",
            fix_hint=f"Pull the model: ollama pull {first_missing}",
        )
    return CheckResult(
        name="Enrichment provider",
        status="OK",
        detail=f"enrichment routes to ollama at {endpoint} ({routed}, reachable)",
    )


def _check_ollama_num_parallel(workspace: Workspace) -> CheckResult | None:
    """Hint that OLLAMA_NUM_PARALLEL must be set server-side for parallel calls.

    NUM_PARALLEL is not detectable via the Ollama API, so when any enrichment
    phase resolves to ollama with `ai.ollama.max_parallel > 1` we surface an
    INFO row spelling out the per-platform set commands. Without a matching
    server-side NUM_PARALLEL, parallel calls queue and count against the
    per-call timeout. Phases route independently, so resolve each rather than
    reading the raw `enrichment.provider` field.
    """
    from daily_driver.core.doctor import CheckResult
    from daily_driver.integrations import ai_provider

    enrichment = _enrichment_cfg(workspace)
    if enrichment is None:
        return None
    ai_cfg = getattr(getattr(workspace, "config", None), "ai", None)
    if ai_cfg is None:
        return None
    any_ollama = (
        ai_provider.resolve_route(ai_cfg, task="fit_notes", domain_cfg=enrichment)[0]
        == "ollama"
    )
    if not any_ollama:
        return None
    max_parallel = ai_cfg.ollama.max_parallel
    if max_parallel <= 1:
        return None
    return CheckResult(
        name="Ollama NUM_PARALLEL",
        status="OK",
        detail=(
            f"enrichment fans out up to {max_parallel} parallel ollama calls; "
            "OLLAMA_NUM_PARALLEL is not detectable via the API. Set it >= "
            f"{max_parallel} server-side or queued calls count against the timeout."
        ),
        fix_hint=(
            f"macOS app: launchctl setenv OLLAMA_NUM_PARALLEL {max_parallel} "
            "(then restart the Ollama app); "
            f"foreground: OLLAMA_NUM_PARALLEL={max_parallel} ollama serve; "
            "systemd: systemctl edit ollama.service and add "
            f'Environment="OLLAMA_NUM_PARALLEL={max_parallel}"'
        ),
    )


def run_checks(workspace: Workspace) -> list[CheckResult]:
    """Return this plugin's doctor rows for the given workspace."""
    results = []
    bak_row = _check_jobs_backups(workspace)
    if bak_row is not None:
        results.append(bak_row)
    pw_row = _check_playwright_browser(workspace)
    if pw_row is not None:
        results.append(pw_row)
    enrich_row = _check_enrichment_provider(workspace)
    if enrich_row is not None:
        results.append(enrich_row)
    parallel_row = _check_ollama_num_parallel(workspace)
    if parallel_row is not None:
        results.append(parallel_row)
    return results
