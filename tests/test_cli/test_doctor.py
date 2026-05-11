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


def test_doctor_on_empty_dir_errors_with_no_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """doctor in an empty dir errors clearly with 'no workspace at <path>'."""
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["doctor"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "no workspace at" in captured.err
    assert str(tmp_path) in captured.err
    assert "daily-driver init" in captured.err
    # No check table is rendered — error short-circuits before checks.
    assert "Python version" not in captured.out + captured.err


def test_doctor_with_bad_workspace_override_errors_with_no_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--workspace pointing to a non-existent path errors with that path."""
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)
    bad_path = tmp_path / "nope"

    rc = app(["--workspace", str(bad_path), "doctor"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "no workspace at" in captured.err
    assert str(bad_path) in captured.err


# ---------------------------------------------------------------------------
# `doctor --fix`
# ---------------------------------------------------------------------------


def test_doctor_fix_regenerates_drifted_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--fix invokes generate without force_overwrite; shows post-fix table."""
    import daily_driver.core.generate as mat_module
    from daily_driver.cli.cli import app
    from daily_driver.core import version_stamp

    ws = _init_workspace(tmp_path)
    # Stamp an old version so drift is detectable; contract files already exist on disk.
    version_stamp.write(ws / ".daily-driver", "0.0.0")

    generate_calls: list[dict] = []

    def spy_generate(
        workspace, *, ignore_drift: bool = False, force_overwrite: bool = False
    ) -> None:
        version_stamp.write(workspace.state_dir, workspace.version)
        generate_calls.append(
            {
                "ignore_drift": ignore_drift,
                "force_overwrite": force_overwrite,
                "root": workspace.root,
            }
        )

    monkeypatch.setattr(mat_module, "generate", spy_generate)

    rc = app(["--workspace", str(ws), "doctor", "--fix"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 0
    assert len(generate_calls) == 1
    # --fix preserves user edits; must not set force_overwrite.
    assert generate_calls[0]["force_overwrite"] is False
    assert "After fix" in combined


def test_doctor_fix_without_workspace_errors_with_no_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--fix without a discoverable workspace errors clearly; nothing to fix."""
    import daily_driver.core.generate as mat_module
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    calls: list[dict] = []
    monkeypatch.setattr(
        mat_module,
        "generate",
        lambda workspace, *, ignore_drift=False, force_overwrite=False: calls.append(
            {"ignore_drift": ignore_drift, "force_overwrite": force_overwrite}
        ),
    )

    rc = app(["doctor", "--fix"])

    captured = capsys.readouterr()
    assert rc == 1
    assert calls == []
    assert "no workspace at" in captured.err
    assert "daily-driver init" in captured.err


def test_doctor_fix_with_bad_workspace_override_errors_without_calling_generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--fix with --workspace pointing nowhere must error before generate runs.
    Guards against a regression where generate is invoked on a None workspace."""
    import daily_driver.core.generate as mat_module
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)
    bad_path = tmp_path / "nope"

    calls: list[dict] = []
    monkeypatch.setattr(
        mat_module,
        "generate",
        lambda workspace, *, ignore_drift=False, force_overwrite=False: calls.append(
            {"ignore_drift": ignore_drift, "force_overwrite": force_overwrite}
        ),
    )

    rc = app(["--workspace", str(bad_path), "doctor", "--fix"])

    captured = capsys.readouterr()
    assert rc == 1
    assert calls == []
    assert "no workspace at" in captured.err
    assert str(bad_path) in captured.err


# ---------------------------------------------------------------------------
# Missing managed files (BUG-1: doctor --fix did not restore deletions)
# ---------------------------------------------------------------------------


def test_doctor_detects_deleted_managed_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plain doctor must report drift when a manifest-tracked file is missing."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)

    victim = ws / ".claude" / "commands" / "daily-driver" / "day-start.md"
    assert victim.is_file(), "init should have materialized day-start.md"
    victim.unlink()

    rc = app(["--workspace", str(ws), "doctor"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # Drift convention is WARNING/exit 0 (matches existing version-stamp drift).
    assert rc == 0
    assert "Workspace drift" in combined
    assert "WARNING" in combined
    assert "missing" in combined.lower()


def test_doctor_fix_restores_deleted_managed_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """doctor --fix must restore deleted managed files from package data."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)

    victim = ws / ".claude" / "commands" / "daily-driver" / "day-start.md"
    original = victim.read_text(encoding="utf-8")
    victim.unlink()
    assert not victim.exists()

    rc = app(["--workspace", str(ws), "doctor", "--fix"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 0
    assert victim.is_file(), "doctor --fix must restore the deleted managed file"
    assert victim.read_text(encoding="utf-8") == original
    assert "Action" in combined or "regenerated" in combined


# ---------------------------------------------------------------------------
# `doctor --reset`
# ---------------------------------------------------------------------------


def test_doctor_reset_regenerates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import daily_driver.core.generate as mat_module
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    generate_calls: list[dict] = []
    monkeypatch.setattr(
        mat_module,
        "generate",
        lambda workspace, *, ignore_drift=False, force_overwrite=False: generate_calls.append(
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
    assert len(generate_calls) == 1
    # --reset must skip drift check and overwrite user edits.
    assert generate_calls[0]["ignore_drift"] is True
    assert generate_calls[0]["force_overwrite"] is True
    assert generate_calls[0]["root"] == ws
    assert "regenerated" in captured.out + captured.err


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
    assert "no workspace at" in captured.err
    assert "daily-driver init" in captured.err


# ---------------------------------------------------------------------------
# Mutually-exclusive --fix / --reset
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AI providers row (ollama reachability)
# ---------------------------------------------------------------------------


def _set_ai_config(ws: Path, block: str) -> None:
    """Append (or replace) the `ai:` block in the workspace's .dd-config.yaml."""
    cfg = ws / ".dd-config.yaml"
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text.rstrip() + "\n" + block + "\n", encoding="utf-8")


def test_doctor_no_ai_row_when_only_claude_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default config (no `ai:` block) must not render the AI providers row."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)

    rc = app(["--workspace", str(ws), "doctor"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "AI providers" not in (captured.out + captured.err)


def test_doctor_ai_row_ok_when_ollama_reachable_with_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import ollama_client

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)
    _set_ai_config(
        ws,
        "ai:\n" "  enrichment:\n" "    provider: ollama\n" "    model: qwen2.5:14b\n",
    )

    monkeypatch.setattr(
        ollama_client, "list_models", lambda endpoint, timeout=5: ["qwen2.5:14b"]
    )

    rc = app(["--workspace", str(ws), "doctor"])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 0
    assert "AI providers" in combined
    assert "OK" in combined
    assert "ollama" in combined.lower()


def test_doctor_ai_row_warning_when_ollama_not_reachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unreachable ollama => WARNING. Inspect CheckResult directly to avoid
    Rich's terminal-width-dependent text wrapping in the rendered table."""
    from daily_driver.core.doctor import run_checks
    from daily_driver.core.workspace import Workspace
    from daily_driver.integrations import ollama_client

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)
    _set_ai_config(
        ws,
        "ai:\n" "  enrichment:\n" "    provider: ollama\n" "    model: qwen2.5:14b\n",
    )

    def _raise(endpoint, timeout=5):
        raise ollama_client.OllamaNotReachableError("not reachable")

    monkeypatch.setattr(ollama_client, "list_models", _raise)

    results = run_checks(Workspace.discover_or_fail(override=ws))
    ai_rows = [r for r in results if r.name == "AI providers"]
    assert len(ai_rows) == 1
    row = ai_rows[0]
    assert row.status == "WARNING"
    assert "not reachable" in row.detail
    assert "ollama serve" in (row.fix_hint or "")


def test_doctor_ai_row_warning_when_model_not_pulled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.core.doctor import run_checks
    from daily_driver.core.workspace import Workspace
    from daily_driver.integrations import ollama_client

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)
    _set_ai_config(
        ws,
        "ai:\n" "  enrichment:\n" "    provider: ollama\n" "    model: qwen2.5:14b\n",
    )

    monkeypatch.setattr(
        ollama_client, "list_models", lambda endpoint, timeout=5: ["phi4:latest"]
    )

    results = run_checks(Workspace.discover_or_fail(override=ws))
    ai_rows = [r for r in results if r.name == "AI providers"]
    assert len(ai_rows) == 1
    row = ai_rows[0]
    assert row.status == "WARNING"
    assert "qwen2.5:14b" in row.detail
    assert "ollama pull qwen2.5:14b" in (row.fix_hint or "")


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


def test_check_ai_providers_warning_when_config_unparseable(tmp_path: Path) -> None:
    """Broken .dd-config.yaml must surface as a WARNING row, NOT silently
    skip the AI providers check. Regression: previously _load_workspace_config
    returned None on any exception, which made _check_ai_providers also
    return None — hiding the issue exactly when the user most needed
    feedback. Tests the function directly because full-CLI workspace
    discovery fails earlier on a malformed config.
    """
    from daily_driver.core.doctor import _check_ai_providers
    from daily_driver.core.workspace import Workspace

    ws = _init_workspace(tmp_path)
    _stamp_workspace(ws)
    workspace = Workspace.discover_or_fail(override=ws)
    # Overwrite with malformed YAML so the AI-providers config load raises.
    (ws / ".dd-config.yaml").write_text(": : : invalid\n", encoding="utf-8")

    result = _check_ai_providers(workspace)
    assert result is not None, "config error must produce a row, not None"
    assert result.status == "WARNING"
    assert result.name == "AI providers"
    assert "failed to parse" in result.detail.lower()
