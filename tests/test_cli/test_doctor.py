"""CLI-level tests for the doctor subcommand.

Exercises `app(["doctor", ...])` end to end, including exit codes, --fix and
--reset dispatch, and workspace discovery. Core check-logic coverage lives
in tests/test_core/test_doctor.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


def _init_workspace(tmp_path: Path) -> Path:
    """Scaffold a workspace the same way the CLI init command does."""
    import argparse

    from daily_driver.cli.commands.init import run

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    ns = argparse.Namespace(
        path=str(ws),
        force=False,
        verbose=False,
        quiet=False,
        no_color=False,
        workspace=None,
    )
    run(ns)
    return ws


def _stamp_workspace(ws: Path) -> None:
    """Write a version stamp so Workspace drift check reports OK."""
    import daily_driver
    from daily_driver.core import version_stamp

    version_stamp.write(ws / ".daily-driver", daily_driver.__version__)


# ---------------------------------------------------------------------------
# Plain `doctor` (no flags)
# ---------------------------------------------------------------------------


def test_doctor_exits_0_when_all_checks_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)

    original_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, **kw: (
            "/usr/local/bin/claude" if name == "claude" else original_which(name, **kw)
        ),
    )

    rc = app(["--workspace", str(ws), "doctor"])

    captured = capsys.readouterr()
    assert rc == 0
    combined = captured.out + captured.err
    assert "Python version" in combined
    assert "dep:pydantic" in combined
    assert "Workspace drift" in combined


def test_doctor_exits_0_with_drifted_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Drift is a WARNING, not ERROR — exit code is 0."""
    from daily_driver.cli.cli import app
    from daily_driver.core import version_stamp

    ws = _init_workspace(tmp_path)
    # Stamp an old version so drift is detected; all contract files remain on disk.
    version_stamp.write(ws / ".daily-driver", "0.0.0")

    rc = app(["--workspace", str(ws), "doctor"])

    captured = capsys.readouterr()
    assert rc == 0
    combined = captured.out + captured.err
    assert "WARNING" in combined
    assert "Workspace drift" in combined


def test_doctor_exits_1_when_required_dep_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required dep missing is ERROR → exit 1."""
    import daily_driver.core.doctor as doctor_module
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)

    from daily_driver.core.doctor import CheckResult

    def fake_checks(workspace):
        return [
            CheckResult(
                name="dep:fake",
                status="ERROR",
                detail="fake not installed",
                fix_hint="pip install fake",
            )
        ]

    monkeypatch.setattr(doctor_module, "run_checks", fake_checks)

    rc = app(["--workspace", str(ws), "doctor"])
    assert rc == 1


def test_doctor_missing_workspace_degrades_gracefully(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--workspace pointing to a non-existent path warns but still runs checks."""
    from daily_driver.cli.cli import app

    # Run from tmp_path so workspace discovery from CWD also fails.
    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(tmp_path / "nope"), "doctor"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "not usable" in combined.lower() or "warning" in combined.lower()
    assert "Python version" in combined
    # No workspace drift check when workspace is None.
    assert "Workspace drift" not in combined
    assert rc in (0, 1)


# ---------------------------------------------------------------------------
# `doctor --fix`
# ---------------------------------------------------------------------------


def test_doctor_fix_rematerializes_drifted_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--fix invokes materialize without force_overwrite; shows post-fix table."""
    import daily_driver.core.materialize as mat_module
    from daily_driver.cli.cli import app
    from daily_driver.core import version_stamp

    ws = _init_workspace(tmp_path)
    # Stamp an old version so drift is detectable; contract files already exist on disk.
    version_stamp.write(ws / ".daily-driver", "0.0.0")

    materialize_calls: list[dict] = []

    def spy_materialize(
        workspace, *, ignore_drift: bool = False, force_overwrite: bool = False
    ) -> None:
        version_stamp.write(workspace.state_dir, workspace.version)
        materialize_calls.append(
            {
                "ignore_drift": ignore_drift,
                "force_overwrite": force_overwrite,
                "root": workspace.root,
            }
        )

    monkeypatch.setattr(mat_module, "materialize", spy_materialize)

    rc = app(["--workspace", str(ws), "doctor", "--fix"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 0
    assert len(materialize_calls) == 1
    # --fix preserves user edits; must not set force_overwrite.
    assert materialize_calls[0]["force_overwrite"] is False
    assert "After fix" in combined


def test_doctor_fix_without_workspace_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--fix without a workspace still runs checks but has nothing to materialize."""
    import daily_driver.core.materialize as mat_module
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    calls: list[dict] = []
    monkeypatch.setattr(
        mat_module,
        "materialize",
        lambda workspace, *, force=False: calls.append({"force": force}),
    )

    # No workspace override, no CWD workspace — workspace resolves to None.
    rc = app(["doctor", "--fix"])

    captured = capsys.readouterr()
    # Without a workspace there is no drift check to fix, so materialize isn't called.
    assert calls == []
    # rc is 0 unless a non-workspace check ERRORs (deps should be OK in test env).
    assert rc in (0, 1)
    assert "After fix" in captured.out + captured.err


# ---------------------------------------------------------------------------
# `doctor --reset`
# ---------------------------------------------------------------------------


def test_doctor_reset_rematerializes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import daily_driver.core.materialize as mat_module
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    materialize_calls: list[dict] = []
    monkeypatch.setattr(
        mat_module,
        "materialize",
        lambda workspace, *, ignore_drift=False, force_overwrite=False: materialize_calls.append(
            {
                "ignore_drift": ignore_drift,
                "force_overwrite": force_overwrite,
                "root": workspace.root,
            }
        ),
    )

    rc = app(["--workspace", str(ws), "doctor", "--reset"])

    captured = capsys.readouterr()
    assert rc == 0
    assert len(materialize_calls) == 1
    # --reset must skip drift check and overwrite user edits.
    assert materialize_calls[0]["ignore_drift"] is True
    assert materialize_calls[0]["force_overwrite"] is True
    assert materialize_calls[0]["root"] == ws
    assert "re-materialized" in captured.out + captured.err


def test_doctor_reset_without_workspace_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--reset requires a workspace; missing workspace exits 1 with clear error."""
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["doctor", "--reset"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "requires a workspace" in captured.err


# ---------------------------------------------------------------------------
# Mutually-exclusive --fix / --reset
# ---------------------------------------------------------------------------


def test_doctor_fix_and_reset_are_mutually_exclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        app(["doctor", "--fix", "--reset"])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "not allowed" in captured.err or "mutually exclusive" in captured.err
