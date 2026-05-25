"""Tests for core/contract.py — init contract definition and check()."""

from __future__ import annotations

import json
from pathlib import Path

from daily_driver.core.contract import MIN_AGENTS, MIN_COMMANDS, check

# ---------------------------------------------------------------------------
# Contract threshold sanity
# ---------------------------------------------------------------------------


def test_min_commands_covers_current_shipped_set() -> None:
    # v0.1.0 ships five commands; regressions (e.g. accidentally removing one)
    # must surface as a contract failure rather than silently shrinking the set.
    assert MIN_COMMANDS >= 5


def test_min_agents_covers_current_shipped_set() -> None:
    assert MIN_AGENTS >= 1


# ---------------------------------------------------------------------------
# check() on a fully-populated workspace returns no violations
# ---------------------------------------------------------------------------


def _scaffold_valid_workspace(root: Path) -> None:
    """Create all files required by the contract for testing."""
    (root / ".dd-config.yaml").write_text(
        "daily_driver:\n  output_dir: .\ntracker:\n  default_category: task\n  categories:\n    task: {required: [title]}\n",
        encoding="utf-8",
    )
    state = root / ".daily-driver"
    state.mkdir()
    (state / "version").write_text("1.0.0", encoding="utf-8")
    (state / "manifest.json").write_text(
        json.dumps({"version": 1, "files": {}}), encoding="utf-8"
    )
    commands_dd = root / ".claude" / "commands" / "daily-driver"
    commands_dd.mkdir(parents=True)
    for name in [
        "day-start.md",
        "day-end.md",
        "check-in.md",
        "summary.md",
        "voice-update.md",
    ]:
        (commands_dd / name).write_text(f"# {name}", encoding="utf-8")
    agents_dd = root / ".claude" / "agents" / "daily-driver"
    agents_dd.mkdir(parents=True)
    (agents_dd / "work-planner.md").write_text("# work-planner", encoding="utf-8")
    (root / ".claude" / "commands" / "user").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "agents" / "user").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.local.json").write_text(
        json.dumps({"metadata": {"daily_driver_version": "1.0.0"}, "permissions": {}}),
        encoding="utf-8",
    )
    (root / "context.md").write_text("context", encoding="utf-8")
    (root / "voice-profile.md").write_text("voice", encoding="utf-8")
    (root / ".gitignore").write_text(
        ".claude/commands/daily-driver/\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Daily Driver Workspace\n", encoding="utf-8")


def test_check_passes_on_valid_workspace(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    violations = check(tmp_path)
    assert violations == [], f"expected no violations, got: {violations}"


# ---------------------------------------------------------------------------
# Missing individual contract entries produce violations
# ---------------------------------------------------------------------------


def _paths(violations: list) -> list[str]:
    return [v.rel_path for v in violations]


def test_check_detects_missing_config(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    (tmp_path / ".dd-config.yaml").unlink()
    assert ".dd-config.yaml" in _paths(check(tmp_path))


def test_check_detects_invalid_config(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    (tmp_path / ".dd-config.yaml").write_text("::: not yaml", encoding="utf-8")
    violations = check(tmp_path)
    matching = [v for v in violations if v.rel_path == ".dd-config.yaml"]
    assert matching, "invalid config must produce a violation"
    assert "parse error" in matching[0].detail.lower()


def test_check_detects_missing_context_md(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    (tmp_path / "context.md").unlink()
    assert "context.md" in _paths(check(tmp_path))


def test_check_detects_missing_voice_profile(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    (tmp_path / "voice-profile.md").unlink()
    assert "voice-profile.md" in _paths(check(tmp_path))


def test_check_detects_missing_settings_json(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    (tmp_path / ".claude" / "settings.local.json").unlink()
    assert ".claude/settings.local.json" in _paths(check(tmp_path))


def test_check_detects_invalid_settings_json(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    (tmp_path / ".claude" / "settings.local.json").write_text(
        "{bad json", encoding="utf-8"
    )
    violations = check(tmp_path)
    matching = [v for v in violations if v.rel_path == ".claude/settings.local.json"]
    assert matching, "invalid settings.local.json must produce a violation"
    assert "json parse error" in matching[0].detail.lower()


def test_check_detects_too_few_commands(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    commands_dir = tmp_path / ".claude" / "commands" / "daily-driver"
    files = list(commands_dir.glob("*.md"))
    for f in files[2:]:
        f.unlink()
    assert ".claude/commands/daily-driver" in _paths(check(tmp_path))


def test_check_detects_missing_agents(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    (tmp_path / ".claude" / "agents" / "daily-driver" / "work-planner.md").unlink()
    assert ".claude/agents/daily-driver" in _paths(check(tmp_path))


def test_check_ignores_missing_user_territory_dirs(tmp_path: Path) -> None:
    """User-territory dirs (.claude/commands/user, .claude/agents/user) are
    not package-managed; their absence must not produce a contract violation
    because `doctor --fix` cannot regenerate them (generate never writes
    user territory). See review-2026-04-23.md #11."""
    _scaffold_valid_workspace(tmp_path)
    import shutil

    shutil.rmtree(tmp_path / ".claude" / "commands" / "user")
    shutil.rmtree(tmp_path / ".claude" / "agents" / "user")
    paths = _paths(check(tmp_path))
    assert ".claude/commands/user" not in paths
    assert ".claude/agents/user" not in paths


def test_check_detects_missing_gitignore(tmp_path: Path) -> None:
    _scaffold_valid_workspace(tmp_path)
    (tmp_path / ".gitignore").unlink()
    assert ".gitignore" in _paths(check(tmp_path))


# ---------------------------------------------------------------------------
# Doctor integration: contract errors appear in run_checks results
# ---------------------------------------------------------------------------


def _fake_workspace(root: Path):
    import logging
    from dataclasses import dataclass

    from rich.console import Console

    state_dir = root / ".daily-driver"

    @dataclass
    class _FakeWorkspace:
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
            # Mirrors the scaffolded `output_dir: .` resolving to the root.
            return self.root

    return _FakeWorkspace(
        root=root,
        state_dir=state_dir,
        version="1.0.0",
        logger=logging.getLogger("test.contract"),
        console=Console(stderr=True),
    )


def test_doctor_run_checks_includes_contract(tmp_path: Path) -> None:
    from daily_driver.core import version_stamp
    from daily_driver.core.doctor import run_checks

    _scaffold_valid_workspace(tmp_path)
    version_stamp.write(tmp_path / ".daily-driver", "1.0.0")
    results = run_checks(_fake_workspace(tmp_path))  # type: ignore[arg-type]
    names = [r.name for r in results]
    assert any(
        "contract" in n.lower() or "Init contract" in n for n in names
    ), f"expected contract check in results, got: {names}"


def test_doctor_run_checks_contract_ok_on_valid_workspace(tmp_path: Path) -> None:
    from daily_driver.core import version_stamp
    from daily_driver.core.doctor import run_checks

    _scaffold_valid_workspace(tmp_path)
    version_stamp.write(tmp_path / ".daily-driver", "1.0.0")
    results = run_checks(_fake_workspace(tmp_path))  # type: ignore[arg-type]
    contract_result = next((r for r in results if r.name == "Init contract"), None)
    assert contract_result is not None, "Init contract check must be present"
    assert contract_result.status == "OK"


def test_doctor_run_checks_contract_error_on_broken_workspace(tmp_path: Path) -> None:
    import shutil

    from daily_driver.core import version_stamp
    from daily_driver.core.doctor import run_checks

    _scaffold_valid_workspace(tmp_path)
    shutil.rmtree(tmp_path / ".claude" / "commands" / "daily-driver")
    version_stamp.write(tmp_path / ".daily-driver", "1.0.0")
    results = run_checks(_fake_workspace(tmp_path))  # type: ignore[arg-type]
    error_results = [
        r for r in results if r.status == "ERROR" and "contract:" in r.name
    ]
    assert error_results, "missing commands must produce contract ERROR results"


def test_doctor_fix_repairs_contract_violation(tmp_path: Path) -> None:
    import shutil

    from daily_driver.core import version_stamp
    from daily_driver.core.doctor import fix, run_checks

    _scaffold_valid_workspace(tmp_path)
    shutil.rmtree(tmp_path / ".claude" / "commands" / "daily-driver")
    version_stamp.write(tmp_path / ".daily-driver", "1.0.0")
    ws = _fake_workspace(tmp_path)

    pre = run_checks(ws)  # type: ignore[arg-type]
    assert any(
        r.status == "ERROR" and r.name.startswith("contract:") for r in pre
    ), "precondition: contract errors present"

    post = fix(pre, ws)  # type: ignore[arg-type]
    contract_errors_post = [
        r for r in post if r.status == "ERROR" and r.name.startswith("contract:")
    ]
    assert (
        not contract_errors_post
    ), f"doctor --fix must clear contract errors; still: {contract_errors_post}"
