"""Doctor checks for installation health and workspace drift.

Pure check logic; no argparse. The CLI wrapper lives in cli/commands/doctor.py.
"""

from __future__ import annotations

import importlib.metadata
import shutil
import sys
from dataclasses import dataclass
from typing import Literal

from daily_driver.core import version_stamp
from daily_driver.core.workspace import Workspace

Status = Literal["OK", "WARNING", "ERROR"]

_REQUIRED_DEPS = ["pydantic", "pyyaml", "rich", "jinja2"]
_OPTIONAL_CLIS = [
    ("claude", "Install from https://claude.ai/download"),
]


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str
    fix_hint: str | None = None
    fixable: bool = False  # can --fix attempt a fix?


def _check_python_version() -> CheckResult:
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 11):
        return CheckResult(
            name="Python version",
            status="OK",
            detail=f"Python {version_str}",
        )
    return CheckResult(
        name="Python version",
        status="ERROR",
        detail=f"Python {version_str} — requires 3.11+",
        fix_hint="Upgrade Python to 3.11 or later.",
    )


def _check_python_deps() -> list[CheckResult]:
    results = []
    for pkg in _REQUIRED_DEPS:
        try:
            version = importlib.metadata.version(pkg)
            results.append(
                CheckResult(
                    name=f"dep:{pkg}",
                    status="OK",
                    detail=f"{pkg} {version}",
                )
            )
        except importlib.metadata.PackageNotFoundError:
            results.append(
                CheckResult(
                    name=f"dep:{pkg}",
                    status="ERROR",
                    detail=f"{pkg} not installed",
                    fix_hint=f"pip install {pkg}",
                )
            )
    return results


def _check_optional_clis() -> list[CheckResult]:
    results = []
    for cmd, hint in _OPTIONAL_CLIS:
        path = shutil.which(cmd)
        if path:
            results.append(
                CheckResult(
                    name=f"cli:{cmd}",
                    status="OK",
                    detail=f"{cmd} found at {path}",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"cli:{cmd}",
                    status="WARNING",
                    detail=f"{cmd} not found on PATH",
                    fix_hint=hint,
                    fixable=False,
                )
            )
    return results


def _check_workspace_drift(workspace: Workspace) -> CheckResult:
    if version_stamp.is_drifted(workspace.state_dir, workspace.version):
        return CheckResult(
            name="Workspace drift",
            status="WARNING",
            detail=f"workspace at {workspace.root} needs re-materialize",
            fix_hint="Run: daily-driver doctor --fix",
            fixable=True,
        )
    return CheckResult(
        name="Workspace drift",
        status="OK",
        detail=f"workspace at {workspace.root} is up to date",
    )


def _check_init_contract(workspace: Workspace) -> list[CheckResult]:
    """Verify every entry in the init contract is satisfied on disk."""
    from daily_driver.core.contract import check as contract_check

    violations = contract_check(workspace.root)
    if not violations:
        return [
            CheckResult(
                name="Init contract",
                status="OK",
                detail="all contract entries satisfied",
            )
        ]
    results = []
    for v in violations:
        results.append(
            CheckResult(
                name=f"contract:{v.rel_path}",
                status="ERROR",
                detail=v.detail,
                fix_hint="Run: daily-driver doctor --fix",
                fixable=True,
            )
        )
    return results


def run_checks(workspace: Workspace | None = None) -> list[CheckResult]:
    """Run all doctor checks. If workspace is None, skip workspace-specific checks."""
    results: list[CheckResult] = []
    results.append(_check_python_version())
    results.extend(_check_python_deps())
    results.extend(_check_optional_clis())
    if workspace is not None:
        results.append(_check_workspace_drift(workspace))
        results.extend(_check_init_contract(workspace))
    return results


def fix(
    results: list[CheckResult], workspace: Workspace | None = None
) -> list[CheckResult]:
    """Attempt to fix all fixable failing checks. Return post-fix re-run results.

    User-edited package-managed files are preserved; only missing or drifted
    (unedited) files are overwritten. Use reset() to unconditionally overwrite.

    ignore_drift is set True so materialize runs even when the version stamp is
    current — a contract violation (e.g. a missing subdirectory) needs the
    materialization body to run regardless of whether the version changed.
    """
    from daily_driver.core.materialize import materialize

    if workspace is not None and any(
        r.fixable
        and r.status != "OK"
        and (r.name == "Workspace drift" or r.name.startswith("contract:"))
        for r in results
    ):
        materialize(workspace, ignore_drift=True, force_overwrite=False)

    return run_checks(workspace)


def reset(workspace: Workspace) -> None:
    """Force re-materialize .claude/ from package data, overwriting user edits."""
    from daily_driver.core.materialize import materialize

    materialize(workspace, ignore_drift=True, force_overwrite=True)
