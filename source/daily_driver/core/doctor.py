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
    from daily_driver.core import manifest

    missing = manifest.missing_files(workspace.root, workspace.state_dir)
    version_drifted = version_stamp.is_drifted(workspace.state_dir, workspace.version)

    if missing:
        n = len(missing)
        preview = ", ".join(missing[:3]) + (", ..." if n > 3 else "")
        return CheckResult(
            name="Workspace drift",
            status="WARNING",
            detail=f"{n} managed file(s) missing from disk: {preview}",
            fix_hint="Run: daily-driver doctor --fix",
            fixable=True,
        )
    if version_drifted:
        return CheckResult(
            name="Workspace drift",
            status="WARNING",
            detail=f"workspace at {workspace.root} needs regeneration",
            fix_hint="Run: daily-driver doctor --fix",
            fixable=True,
        )
    return CheckResult(
        name="Workspace drift",
        status="OK",
        detail=f"workspace at {workspace.root} is up to date",
    )


def _check_daily_state_writable(workspace: Workspace) -> CheckResult:
    """Daily-state YAML lives at <ephemeral_dir>/daily/; verify the dir is writable."""
    import tempfile

    daily_dir = workspace.ephemeral_dir / "daily"
    try:
        daily_dir.mkdir(parents=True, exist_ok=True)
        # NamedTemporaryFile cleans up via context manager even if write/close
        # raises mid-flight, so the probe never leaks a stray dotfile on the
        # error path.
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=".doctor-write-probe-",
            dir=daily_dir,
            delete=True,
        ):
            pass
    except OSError as exc:
        return CheckResult(
            name="Daily-state writable",
            status="ERROR",
            detail=f"{daily_dir} is not writable: {exc}",
            fix_hint=f"Check filesystem permissions on {daily_dir}",
            fixable=False,
        )
    return CheckResult(
        name="Daily-state writable",
        status="OK",
        detail=f"{daily_dir} is writable",
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


def _load_workspace_config(workspace: Workspace):  # type: ignore[no-untyped-def]
    """Load `.dd-config.yaml` for a workspace.

    Returns the parsed Config on success, the literal string "missing" when
    no config file exists, or the exception object when parsing fails so
    callers can render a useful diagnostic row instead of silently
    omitting it.
    """
    from daily_driver.core.config import load as load_config

    cfg_path = workspace.root / ".dd-config.yaml"
    if not cfg_path.is_file():
        return "missing"
    try:
        return load_config(cfg_path)
    except Exception as exc:  # noqa: BLE001
        return exc


def _check_ai_providers(workspace: Workspace) -> CheckResult | None:
    """Verify Ollama reachability + model presence for any task routed to it.

    Returns None (no row emitted) when every task uses the claude default.
    Only one row is rendered overall, summarizing all ollama-routed tasks.
    Drift convention: failures are WARNING (exit 0), matching the workspace
    drift / contract checks added in PR #30.
    """
    cfg = _load_workspace_config(workspace)
    if cfg == "missing":
        return None
    if isinstance(cfg, Exception):
        # User has a broken .dd-config.yaml. Don't silently skip the AI
        # row — that's exactly the failure mode where a misrouted task
        # (e.g. typo'd `ai.enrichment.provdier: ollama`) would surprise
        # the user with no explanation.
        return CheckResult(
            name="AI providers",
            status="WARNING",
            detail=f".dd-config.yaml failed to parse: {cfg}",
            fix_hint="Fix the YAML / schema in .dd-config.yaml.",
            fixable=False,
        )

    ai_cfg = cfg.ai
    ollama_tasks: list[tuple[str, str]] = []
    for task_name in ("enrichment", "summary"):
        task_cfg = getattr(ai_cfg, task_name)
        if task_cfg.provider == "ollama":
            model = task_cfg.model or "qwen2.5:14b"
            ollama_tasks.append((task_name, model))
    if not ollama_tasks:
        return None

    from daily_driver.integrations import ollama_client
    from daily_driver.integrations.ollama_client import OllamaNotReachableError

    endpoint = ai_cfg.ollama.endpoint
    summary = ", ".join(f"{t}: ollama {m}" for t, m in ollama_tasks)

    try:
        pulled = ollama_client.list_models(endpoint, timeout=5)
    except OllamaNotReachableError:
        return CheckResult(
            name="AI providers",
            status="WARNING",
            detail=f"ollama at {endpoint} not reachable ({summary})",
            fix_hint="Start the server: ollama serve",
            fixable=False,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="AI providers",
            status="WARNING",
            detail=f"ollama at {endpoint} returned an error: {exc}",
            fix_hint="Verify `ollama serve` is healthy on the configured endpoint.",
            fixable=False,
        )

    missing = [(t, m) for t, m in ollama_tasks if m not in pulled]
    if missing:
        miss_str = ", ".join(f"{m} ({t})" for t, m in missing)
        first_missing_model = missing[0][1]
        return CheckResult(
            name="AI providers",
            status="WARNING",
            detail=f"ollama reachable at {endpoint}; model(s) not pulled: {miss_str}",
            fix_hint=f"Pull the model: ollama pull {first_missing_model}",
            fixable=False,
        )
    return CheckResult(
        name="AI providers",
        status="OK",
        detail=(
            f"ollama at {endpoint} reachable; {summary}, "
            f"parallel={ai_cfg.ollama.max_parallel}"
        ),
    )


def _plugin_checks(workspace: Workspace) -> list[CheckResult]:
    """Run each plugin's doctor checks.

    Plugin check functions are resolved by dotted path and imported lazily so
    core never eagerly loads plugin implementation modules at import time.
    """
    import importlib

    from daily_driver.plugins import PLUGINS

    results: list[CheckResult] = []
    for plugin in PLUGINS:
        if plugin.doctor_checks is None:
            continue
        module_path, _, attr = plugin.doctor_checks.rpartition(".")
        run = getattr(importlib.import_module(module_path), attr)
        results.extend(run(workspace))
    return results


def run_checks(workspace: Workspace | None = None) -> list[CheckResult]:
    """Run all doctor checks. If workspace is None, skip workspace-specific checks."""
    results: list[CheckResult] = []
    results.append(_check_python_version())
    results.extend(_check_python_deps())
    results.extend(_check_optional_clis())
    if workspace is not None:
        results.append(_check_workspace_drift(workspace))
        results.append(_check_daily_state_writable(workspace))
        results.extend(_check_init_contract(workspace))
        ai_row = _check_ai_providers(workspace)
        if ai_row is not None:
            results.append(ai_row)
        results.extend(_plugin_checks(workspace))
    return results


def fix(
    results: list[CheckResult], workspace: Workspace | None = None
) -> list[CheckResult]:
    """Attempt to fix all fixable failing checks. Return post-fix re-run results.

    User-edited package-managed files are preserved; only missing or drifted
    (unedited) files are overwritten. Use reset() to unconditionally overwrite.

    ignore_drift is set True so generate runs even when the version stamp is
    current — a contract violation (e.g. a missing subdirectory) needs the
    generation body to run regardless of whether the version changed.
    """
    from daily_driver.core.generate import generate

    if workspace is not None and any(
        r.fixable
        and r.status != "OK"
        and (r.name == "Workspace drift" or r.name.startswith("contract:"))
        for r in results
    ):
        generate(workspace, ignore_drift=True, force_overwrite=False)

    return run_checks(workspace)


def reset(workspace: Workspace) -> None:
    """Force regenerate .claude/ from package data, overwriting user edits."""
    from daily_driver.core.generate import generate

    generate(workspace, ignore_drift=True, force_overwrite=True)
