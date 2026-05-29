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

    @property
    def output_dir(self) -> Path:
        # No config block here; treat the root as the durable output dir.
        return self.root

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
    # Not auto-fixable: carries a manual hint, no plugin fixer.
    assert claude_result.plugin_fixer is None


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
# 4. After generate(workspace), drift check is OK
# ---------------------------------------------------------------------------


def test_workspace_drift_ok_after_generate(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    # Write the stamp directly (simulating a completed generate).
    version_stamp.write(ws.state_dir, ws.version)

    results = run_checks(ws)  # type: ignore[arg-type]
    drift = next((r for r in results if r.name == "Workspace drift"), None)
    assert drift is not None
    assert drift.status == "OK"


# ---------------------------------------------------------------------------
# 5. reset(workspace) calls generate with ignore_drift=True, force_overwrite=True
# ---------------------------------------------------------------------------


def test_reset_calls_generate_with_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reset() must pass ignore_drift=True and force_overwrite=True to generate."""
    import daily_driver.core.generate as mat_module

    ws = _FakeWorkspace.make(tmp_path)
    generate_calls: list[dict] = []

    def spy_generate(
        workspace, *, ignore_drift: bool = False, force_overwrite: bool = False
    ) -> None:
        generate_calls.append(
            {
                "workspace": workspace,
                "ignore_drift": ignore_drift,
                "force_overwrite": force_overwrite,
            }
        )

    monkeypatch.setattr(mat_module, "generate", spy_generate)

    reset(ws)  # type: ignore[arg-type]

    assert len(generate_calls) == 1
    assert generate_calls[0]["ignore_drift"] is True
    assert generate_calls[0]["force_overwrite"] is True
    assert generate_calls[0]["workspace"] is ws


# ---------------------------------------------------------------------------
# 6. fix() calls generate without force_overwrite; preserves user edits
# ---------------------------------------------------------------------------


def test_fix_resolves_workspace_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import daily_driver.core.generate as mat_module

    ws = _FakeWorkspace.make(tmp_path)
    # No stamp — drift present.

    generate_calls: list[dict] = []

    def spy_generate(
        workspace, *, ignore_drift: bool = False, force_overwrite: bool = False
    ) -> None:
        # Actually write the stamp so the re-run check passes.
        version_stamp.write(workspace.state_dir, workspace.version)
        generate_calls.append(
            {
                "workspace": workspace,
                "ignore_drift": ignore_drift,
                "force_overwrite": force_overwrite,
            }
        )

    monkeypatch.setattr(mat_module, "generate", spy_generate)

    initial = run_checks(ws)  # type: ignore[arg-type]
    drift_before = next(r for r in initial if r.name == "Workspace drift")
    assert drift_before.status == "WARNING"

    post_fix = fix(initial, ws)  # type: ignore[arg-type]

    assert len(generate_calls) == 1
    # fix() must NOT set force_overwrite — user edits are preserved.
    assert generate_calls[0]["force_overwrite"] is False

    drift_after = next(r for r in post_fix if r.name == "Workspace drift")
    assert drift_after.status == "OK"


# ---------------------------------------------------------------------------
# 7. README.md is package-managed: preserved on fix, overwritten on reset
# ---------------------------------------------------------------------------


def test_fix_preserves_user_edited_readme(tmp_path: Path) -> None:
    """doctor --fix must not overwrite README.md when the user has edited it."""
    from daily_driver.core import generate as gen_mod

    ws = _FakeWorkspace.make(tmp_path)

    # First generate writes README.md and records its SHA.
    gen_mod.generate(ws, ignore_drift=True, force_overwrite=True)
    readme = ws.root / "README.md"
    assert readme.exists()

    # Simulate user edit.
    readme.write_text("# My custom README\n\nI edited this.\n", encoding="utf-8")

    # Drift stamp so fix() triggers regenerate.
    version_stamp.write(ws.state_dir, "0.9.0")

    # fix() calls generate with force_overwrite=False — user edit must survive.
    from daily_driver.core.doctor import fix, run_checks

    results = run_checks(ws)  # type: ignore[arg-type]
    fix(results, ws)  # type: ignore[arg-type]

    assert (
        readme.read_text(encoding="utf-8") == "# My custom README\n\nI edited this.\n"
    )


def test_reset_overwrites_user_edited_readme(tmp_path: Path) -> None:
    """doctor --reset must overwrite README.md even when the user has edited it."""
    from daily_driver.core import generate as gen_mod
    from daily_driver.core.doctor import reset

    ws = _FakeWorkspace.make(tmp_path)

    gen_mod.generate(ws, ignore_drift=True, force_overwrite=True)
    readme = ws.root / "README.md"
    original_content = readme.read_text(encoding="utf-8")

    # Simulate user edit.
    readme.write_text("# custom\n", encoding="utf-8")

    reset(ws)  # type: ignore[arg-type]

    restored = readme.read_text(encoding="utf-8")
    assert (
        restored == original_content
    ), "reset must restore README.md to package content"


# ---------------------------------------------------------------------------
# _run_plugin_fixers: plugin-supplied fixers
# ---------------------------------------------------------------------------


def test_run_plugin_fixers_runs_only_failing_rows_with_a_fixer() -> None:
    from daily_driver.core.doctor import _run_plugin_fixers

    calls: list[str] = []
    rows = [
        # OK row with a fixer attached must be skipped on status alone.
        CheckResult("ok", "OK", "", plugin_fixer=lambda: calls.append("ok")),
        # Failing row with no fixer must be skipped (nothing to call).
        CheckResult("nofix", "WARNING", "", plugin_fixer=None),
        CheckResult("pw", "WARNING", "", plugin_fixer=lambda: calls.append("pw")),
    ]

    repaired = _run_plugin_fixers(rows)

    assert calls == ["pw"]
    assert repaired == ["pw"]


def test_run_plugin_fixers_swallows_raising_fixer(caplog) -> None:
    from daily_driver.core.doctor import _run_plugin_fixers

    def boom() -> None:
        raise RuntimeError("install failed")

    rows = [
        CheckResult("a", "WARNING", "", plugin_fixer=boom),
        CheckResult("b", "WARNING", "", plugin_fixer=lambda: None),
    ]

    repaired = _run_plugin_fixers(rows)

    # A raising fixer must not abort the batch nor land in repaired.
    assert repaired == ["b"]
    assert "fix for a failed" in caplog.text


def test_run_plugin_fixers_prefers_stderr_detail(caplog) -> None:
    from daily_driver.core.doctor import _run_plugin_fixers
    from daily_driver.integrations.playwright import PlaywrightError

    def boom() -> None:
        raise PlaywrightError(1, ["playwright"], stderr="disk full")

    rows = [CheckResult("pw", "WARNING", "", plugin_fixer=boom)]

    assert _run_plugin_fixers(rows) == []
    assert "disk full" in caplog.text
