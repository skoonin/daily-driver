from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from rich.console import Console

from daily_driver.core import version_stamp
from daily_driver.core.doctor import CheckResult, fix, reset, run_checks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeWorkspace:
    """Minimal workspace stand-in that skips config-loading / discovery machinery."""

    root: Path
    state_dir: Path
    version: str
    logger: logging.Logger
    console: Console

    @property
    def ephemeral_dir(self) -> Path:
        return self.state_dir / "state"

    @classmethod
    def make(cls, root: Path, version: str = "1.0.0") -> _FakeWorkspace:
        state_dir = root / ".daily-driver"
        state_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            state_dir=state_dir,
            version=version,
            logger=logging.getLogger("test.doctor"),
            console=Console(stderr=True),
        )


# ---------------------------------------------------------------------------
# 1. run_checks(None) returns list with python-version + dep checks
# ---------------------------------------------------------------------------


def test_run_checks_none_returns_list() -> None:
    results = run_checks(None)
    assert isinstance(results, list)
    assert len(results) > 0
    assert all(isinstance(r, CheckResult) for r in results)


def test_run_checks_none_contains_python_version() -> None:
    results = run_checks(None)
    names = [r.name for r in results]
    assert "Python version" in names


def test_run_checks_none_contains_required_deps() -> None:
    results = run_checks(None)
    names = [r.name for r in results]
    for pkg in ["pydantic", "pyyaml", "rich", "jinja2"]:
        assert f"dep:{pkg}" in names, f"expected dep:{pkg} in results"


def test_run_checks_none_deps_ok_in_test_env() -> None:
    """All required deps are installed in the test venv."""
    results = run_checks(None)
    for r in results:
        if r.name.startswith("dep:"):
            assert (
                r.status == "OK"
            ), f"{r.name} should be OK in test env, got {r.status}: {r.detail}"


def test_run_checks_none_no_workspace_drift_check() -> None:
    results = run_checks(None)
    names = [r.name for r in results]
    assert "Workspace drift" not in names


# ---------------------------------------------------------------------------
# 2. Mock shutil.which("claude") returning None → WARNING
# ---------------------------------------------------------------------------


def test_claude_cli_warning_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    original_which = shutil.which

    def patched_which(name: str, **kwargs):
        if name == "claude":
            return None
        return original_which(name, **kwargs)

    monkeypatch.setattr(shutil, "which", patched_which)

    results = run_checks(None)
    claude_result = next((r for r in results if r.name == "cli:claude"), None)
    assert claude_result is not None
    assert claude_result.status == "WARNING"
    assert claude_result.fixable is False


def test_claude_cli_ok_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    original_which = shutil.which

    def patched_which(name: str, **kwargs):
        if name == "claude":
            return "/usr/local/bin/claude"
        return original_which(name, **kwargs)

    monkeypatch.setattr(shutil, "which", patched_which)

    results = run_checks(None)
    claude_result = next((r for r in results if r.name == "cli:claude"), None)
    assert claude_result is not None
    assert claude_result.status == "OK"


# ---------------------------------------------------------------------------
# 3. Workspace drift: fresh Workspace.init → drift check is WARNING
# ---------------------------------------------------------------------------


def test_workspace_drift_warning_when_no_stamp(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    # No stamp written — drift should be detected.
    results = run_checks(ws)  # type: ignore[arg-type]
    drift = next((r for r in results if r.name == "Workspace drift"), None)
    assert drift is not None
    assert drift.status == "WARNING"
    assert drift.fixable is True
    assert "doctor --fix" in (drift.fix_hint or "")


def test_daily_state_writable_ok_in_fresh_workspace(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    results = run_checks(ws)  # type: ignore[arg-type]
    check = next((r for r in results if r.name == "Daily-state writable"), None)
    assert check is not None
    assert check.status == "OK"
    # Probe must clean up after itself.
    assert not (ws.state_dir / "state" / "daily" / ".doctor-write-probe").exists()


def test_daily_state_writable_error_when_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pathlib

    ws = _FakeWorkspace.make(tmp_path)

    real_mkdir = pathlib.Path.mkdir

    def selective_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "daily" and self.parent.name == "state":
            raise PermissionError("simulated permission denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pathlib.Path, "mkdir", selective_mkdir)

    results = run_checks(ws)  # type: ignore[arg-type]
    check = next((r for r in results if r.name == "Daily-state writable"), None)
    assert check is not None
    assert check.status == "ERROR"
    assert (
        "permission denied" in check.detail.lower()
        or "not writable" in check.detail.lower()
    )


# ---------------------------------------------------------------------------
# 4. After materialize(workspace), drift check is OK
# ---------------------------------------------------------------------------


def test_workspace_drift_ok_after_materialize(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    # Write the stamp directly (simulating a completed materialize).
    version_stamp.write(ws.state_dir, ws.version)

    results = run_checks(ws)  # type: ignore[arg-type]
    drift = next((r for r in results if r.name == "Workspace drift"), None)
    assert drift is not None
    assert drift.status == "OK"


# ---------------------------------------------------------------------------
# 5. reset(workspace) calls materialize with ignore_drift=True, force_overwrite=True
# ---------------------------------------------------------------------------


def test_reset_calls_materialize_with_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reset() must pass ignore_drift=True and force_overwrite=True to materialize."""
    import daily_driver.core.materialize as mat_module

    ws = _FakeWorkspace.make(tmp_path)
    materialize_calls: list[dict] = []

    def spy_materialize(
        workspace, *, ignore_drift: bool = False, force_overwrite: bool = False
    ) -> None:
        materialize_calls.append(
            {
                "workspace": workspace,
                "ignore_drift": ignore_drift,
                "force_overwrite": force_overwrite,
            }
        )

    monkeypatch.setattr(mat_module, "materialize", spy_materialize)

    reset(ws)  # type: ignore[arg-type]

    assert len(materialize_calls) == 1
    assert materialize_calls[0]["ignore_drift"] is True
    assert materialize_calls[0]["force_overwrite"] is True
    assert materialize_calls[0]["workspace"] is ws


# ---------------------------------------------------------------------------
# 6. fix() calls materialize without force_overwrite; preserves user edits
# ---------------------------------------------------------------------------


def test_fix_resolves_workspace_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import daily_driver.core.materialize as mat_module

    ws = _FakeWorkspace.make(tmp_path)
    # No stamp — drift present.

    materialize_calls: list[dict] = []

    def spy_materialize(
        workspace, *, ignore_drift: bool = False, force_overwrite: bool = False
    ) -> None:
        # Actually write the stamp so the re-run check passes.
        version_stamp.write(workspace.state_dir, workspace.version)
        materialize_calls.append(
            {
                "workspace": workspace,
                "ignore_drift": ignore_drift,
                "force_overwrite": force_overwrite,
            }
        )

    monkeypatch.setattr(mat_module, "materialize", spy_materialize)

    initial = run_checks(ws)  # type: ignore[arg-type]
    drift_before = next(r for r in initial if r.name == "Workspace drift")
    assert drift_before.status == "WARNING"

    post_fix = fix(initial, ws)  # type: ignore[arg-type]

    assert len(materialize_calls) == 1
    # fix() must NOT set force_overwrite — user edits are preserved.
    assert materialize_calls[0]["force_overwrite"] is False

    drift_after = next(r for r in post_fix if r.name == "Workspace drift")
    assert drift_after.status == "OK"
