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


def _check_jobs_csv_custom_columns(workspace: Workspace) -> CheckResult | None:
    """Report jobs.csv columns outside the canonical header.

    Headers are never migrated: an unrecognized column is carried through every
    rewrite verbatim, which is what keeps a deliberately hand-added column
    alive. Two different mechanisms do it -- ``jobs run`` hands the sink the raw
    on-disk header, extras included, while ``run_backfill`` tracks them
    separately as ``extra_columns``. The same carry-through silently
    preserves one left behind by a rename, and nothing here can tell the two
    apart — so this reports OK, not WARNING: a column you meant to keep must
    not raise a row you can never clear.
    """
    from daily_driver.core.doctor import CheckResult
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        read_rows,
    )

    jobs_csv = workspace.root / "jobs.csv"
    if not jobs_csv.exists():
        return None
    header, _rows = read_rows(jobs_csv)
    extra = [column for column in header if column not in CANONICAL_HEADER]
    if not extra:
        return None
    return CheckResult(
        name="Jobs CSV columns",
        status="OK",
        detail=(
            f"{jobs_csv} carries {len(extra)} column(s) daily-driver does not "
            f"write: {', '.join(extra)}. They are preserved on every rewrite."
        ),
        fix_hint=(
            "Keep them if they are yours. Otherwise delete the column(s) by "
            "hand — no automatic migration exists"
        ),
    )


def _check_jobs_backups(workspace: Workspace) -> CheckResult | None:
    """Warn if `jobs.csv.bak.*` snapshots have accumulated under backups/.

    Each backfill / migration run drops a `.bak.<utc-stamp>` snapshot before
    mutating jobs.csv. Users won't notice them, so over months of use they
    pile up.
    """
    from daily_driver.core.doctor import CheckResult

    backups_dir = workspace.root / "backups"
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
    probe = pw.probe_browser(engine)
    if probe.installed:
        return None
    display = _ENGINE_DISPLAY.get(engine, engine.capitalize())
    sources = ", ".join(enabled)
    # Playwright pins a browser build revision per release, so two interpreters
    # on different playwright versions want different builds from the same
    # cache. Name the interpreter, and qualify every manual command with it: a
    # bare `playwright` on PATH can be a different install than the one probed,
    # and installing there leaves this warning standing.
    if probe.playwright_error is not None:
        # No plugin_fixer: --fix downloads a browser, which cannot repair a
        # playwright that could not answer in the first place.
        return CheckResult(
            name="Playwright browser",
            status="WARNING",
            detail=(
                f"cannot tell whether the {display} browser is installed for "
                f"{sys.executable}: {probe.playwright_error}; "
                f"source(s) {sources} will fail at launch"
            ),
            fix_hint=(
                "Repair playwright: pipx reinstall daily-driver "
                f"(pip installs: {sys.executable} -m pip install "
                "--force-reinstall playwright)"
            ),
        )
    return CheckResult(
        name="Playwright browser",
        status="WARNING",
        detail=(
            f"{display} browser not installed for {sys.executable}; "
            f"source(s) {sources} will fail at launch"
        ),
        fix_hint=(
            "Run: daily-driver doctor --fix "
            f"(or: {sys.executable} -m playwright install {engine})"
        ),
        plugin_fixer=lambda: pw.install_browser(engine),
    )


def _check_board_sources_have_boards(workspace: Workspace) -> CheckResult | None:
    """Warn when an enabled board source has no boards to scrape.

    Greenhouse, Ashby and Lever scrape (pinned config slugs ∪ discover-boards
    matched cache). With no pins and no sweep on disk the source runs a clean,
    empty scrape, which is easy to mistake for "no matching roles".
    """
    from daily_driver.core.doctor import CheckResult
    from daily_driver.plugins.job_search.scraper.discovery import (
        SWEEP_PLATFORMS,
        load_matched_boards,
    )

    config = getattr(workspace, "config", None)
    job_cfg = getattr(getattr(config, "plugins", None), "job_search", None)
    if job_cfg is None:
        return None
    empty: list[str] = []
    for platform in SWEEP_PLATFORMS:
        toggle = job_cfg.sources.get(platform)
        if toggle is None or not toggle.enabled:
            continue
        if getattr(toggle, f"{platform}_boards", None):
            continue
        if load_matched_boards(workspace.ephemeral_dir, platform):
            continue
        empty.append(platform)
    if not empty:
        return None
    return CheckResult(
        name="Job boards",
        status="WARNING",
        detail=(
            f"{', '.join(empty)} enabled but no boards to scrape: none pinned "
            "in config and no discover-boards sweep has run"
        ),
        fix_hint="Run: daily-driver jobs discover-boards",
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
    boards_row = _check_board_sources_have_boards(workspace)
    if boards_row is not None:
        results.append(boards_row)
    columns_row = _check_jobs_csv_custom_columns(workspace)
    if columns_row is not None:
        results.append(columns_row)
    return results
