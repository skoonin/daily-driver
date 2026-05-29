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
        fixable=False,
    )


def _enabled_playwright_sources(workspace: Workspace) -> list[str]:
    """Names of configured-on sources that require the Playwright browser.

    A source counts as enabled only when explicitly toggled on, matching the
    runtime gate in ``runner.run_scrapers`` (an absent toggle is off).
    """
    from daily_driver.plugins.job_search.scraper.runner import _PLAYWRIGHT_SOURCES

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


def _check_playwright_browser(workspace: Workspace) -> CheckResult | None:
    """Warn when a Playwright source is enabled but its Firefox build is missing.

    The ``playwright`` pip package installs without the ~100 MB browser binary
    (wheels run no install-time code), so an enabled Apple source dies at
    launch. Gated to macOS — the only platform the launchers target — and
    emitted only when such a source is actually configured on, so users who do
    not run Playwright sources never see browser noise.
    """
    from daily_driver.core.doctor import CheckResult
    from daily_driver.integrations import playwright as pw

    if sys.platform != "darwin":
        return None
    enabled = _enabled_playwright_sources(workspace)
    if not enabled:
        return None
    if pw.firefox_installed():
        return None
    return CheckResult(
        name="Playwright browser",
        status="WARNING",
        detail=(
            f"Firefox browser not installed; source(s) {', '.join(enabled)} "
            f"will fail at launch"
        ),
        fix_hint="Run: daily-driver doctor --fix (or: playwright install firefox)",
        fixable=True,
        fix_action=pw.install_firefox,
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
    return results
