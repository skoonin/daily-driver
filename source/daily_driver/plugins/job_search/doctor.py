"""Job-search doctor checks contributed to the core doctor run.

Core doctor imports ``run_checks`` lazily (via the plugin's ``doctor_checks``
dotted path), so this module is never loaded for a doctor run that does not
exercise plugin health checks.
"""

from __future__ import annotations

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


def run_checks(workspace: Workspace) -> list[CheckResult]:
    """Return this plugin's doctor rows for the given workspace."""
    results = []
    bak_row = _check_jobs_backups(workspace)
    if bak_row is not None:
        results.append(bak_row)
    return results
