"""End-to-end tests — invoke the installed CLI as a subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run `python -m daily_driver <args>` and return the completed process."""
    return subprocess.run(
        [sys.executable, "-m", "daily_driver", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """Fresh initialized workspace; return its path."""
    result = _run(["init", str(tmp_path)])
    assert result.returncode == 0, f"init failed: {result.stderr}"
    return tmp_path


# ---------------------------------------------------------------------------
# 1. --version
# ---------------------------------------------------------------------------


def test_version_flag() -> None:
    result = _run(["--version"])
    assert result.returncode == 0
    assert "daily-driver" in result.stdout
    # version-like: at least one digit and dot (e.g. 0.1.0.dev0)
    assert any(c.isdigit() for c in result.stdout)
    assert "." in result.stdout


# ---------------------------------------------------------------------------
# 2. Bare invocation
# ---------------------------------------------------------------------------


def test_bare_invocation_prints_help() -> None:
    result = _run([])
    assert result.returncode == 2
    combined = (result.stdout + result.stderr).lower()
    assert "usage:" in combined or "commands" in combined


# ---------------------------------------------------------------------------
# 3. init creates expected artifacts
# ---------------------------------------------------------------------------


def test_init_creates_workspace(tmp_path: Path) -> None:
    result = _run(["init", str(tmp_path)])
    assert result.returncode == 0, f"init failed: {result.stderr}"
    assert (tmp_path / ".dd-config.yaml").exists()
    assert (tmp_path / ".daily-driver").is_dir()


# ---------------------------------------------------------------------------
# 4. init --force succeeds after first init
# ---------------------------------------------------------------------------


def test_init_force_overwrites(tmp_path: Path) -> None:
    first = _run(["init", str(tmp_path)])
    assert first.returncode == 0, f"first init failed: {first.stderr}"

    second = _run(["init", "--force", str(tmp_path)])
    assert second.returncode == 0, f"init --force failed: {second.stderr}"


# ---------------------------------------------------------------------------
# 5. tracker add then list --json
# ---------------------------------------------------------------------------


def test_tracker_add_then_list_json(initialized_workspace: Path) -> None:
    ws = str(initialized_workspace)
    add = _run(
        [
            "--workspace",
            ws,
            "tracker",
            "add",
            "--category",
            "task",
            "--title",
            "E2E task",
        ]
    )
    assert add.returncode == 0, f"add failed: {add.stderr}"

    lst = _run(["--workspace", ws, "tracker", "list", "--json"])
    assert lst.returncode == 0, f"list failed: {lst.stderr}"

    parsed = json.loads(lst.stdout)
    assert parsed["schema"] == 1
    entries = parsed["data"]["entries"]
    assert isinstance(entries, list)
    assert len(entries) == 1
    assert entries[0]["title"] == "E2E task"
    assert entries[0]["category"] == "task"


# ---------------------------------------------------------------------------
# 6. tracker add with --extra KEY=VALUE pairs
# ---------------------------------------------------------------------------


def test_tracker_add_with_extras(initialized_workspace: Path) -> None:
    ws = str(initialized_workspace)
    add = _run(
        [
            "--workspace",
            ws,
            "tracker",
            "add",
            "--category",
            "task",
            "--title",
            "Task with extras",
            "--extra",
            "comp=200",
            "--extra",
            "source=linkedin",
        ]
    )
    assert add.returncode == 0, f"add failed: {add.stderr}"

    lst = _run(["--workspace", ws, "tracker", "list", "--json"])
    assert lst.returncode == 0, f"list failed: {lst.stderr}"

    parsed = json.loads(lst.stdout)
    entries = parsed["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["extras"] == {"comp": "200", "source": "linkedin"}


# ---------------------------------------------------------------------------
# 7. tracker update status
# ---------------------------------------------------------------------------


def test_tracker_update_status(initialized_workspace: Path) -> None:
    ws = str(initialized_workspace)
    _run(
        [
            "--workspace",
            ws,
            "tracker",
            "add",
            "--category",
            "task",
            "--title",
            "Updatable task",
        ]
    )

    update = _run(
        ["--workspace", ws, "tracker", "update", "task-001", "--status", "done"]
    )
    assert update.returncode == 0, f"update failed: {update.stderr}"

    lst = _run(["--workspace", ws, "tracker", "list", "--json"])
    parsed = json.loads(lst.stdout)
    entries = parsed["data"]["entries"]
    assert entries[0]["status"] == "done"


# ---------------------------------------------------------------------------
# 8. tracker list --tag filters to matching entries only
# ---------------------------------------------------------------------------


def test_tracker_filter_by_tag(initialized_workspace: Path) -> None:
    ws = str(initialized_workspace)
    _run(
        [
            "--workspace",
            ws,
            "tracker",
            "add",
            "--category",
            "task",
            "--title",
            "Alpha task",
            "--tags",
            "alpha",
        ]
    )
    _run(
        [
            "--workspace",
            ws,
            "tracker",
            "add",
            "--category",
            "task",
            "--title",
            "Beta task",
            "--tags",
            "beta",
        ]
    )

    lst = _run(["--workspace", ws, "tracker", "list", "--tag", "alpha", "--json"])
    assert lst.returncode == 0, f"list --tag failed: {lst.stderr}"

    parsed = json.loads(lst.stdout)
    entries = parsed["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["title"] == "Alpha task"


# ---------------------------------------------------------------------------
# 9. tracker stats shows counts
# ---------------------------------------------------------------------------


def test_tracker_stats_shows_counts(initialized_workspace: Path) -> None:
    ws = str(initialized_workspace)
    _run(
        [
            "--workspace",
            ws,
            "tracker",
            "add",
            "--category",
            "task",
            "--title",
            "Task one",
        ]
    )
    _run(
        [
            "--workspace",
            ws,
            "tracker",
            "add",
            "--category",
            "task",
            "--title",
            "Task two",
        ]
    )
    _run(
        ["--workspace", ws, "tracker", "add", "--category", "job", "--title", "Job one"]
    )

    stats = _run(["--workspace", ws, "tracker", "stats"])
    assert stats.returncode == 0, f"stats failed: {stats.stderr}"

    output = stats.stdout
    assert "2" in output  # 2 tasks
    assert "1" in output  # 1 job
    assert "3" in output  # 3 total


# ---------------------------------------------------------------------------
# 10. status --json structure
# ---------------------------------------------------------------------------


def test_status_json_structure(initialized_workspace: Path) -> None:
    ws = str(initialized_workspace)
    _run(
        [
            "--workspace",
            ws,
            "tracker",
            "add",
            "--category",
            "task",
            "--title",
            "Status task",
        ]
    )

    status = _run(["--workspace", ws, "status", "--json"])
    assert status.returncode == 0, f"status failed: {status.stderr}"

    envelope = json.loads(status.stdout)
    assert envelope["schema"] == 1
    data = envelope["data"]
    assert "totals" in data, "status JSON must contain 'totals'"
    assert "stalled" in data, "status JSON must contain 'stalled'"
    assert "recent" in data, "status JSON must contain 'recent'"
    assert data["totals"]["total"] == 1


# ---------------------------------------------------------------------------
# 11. doctor in initialized workspace exits 0
# ---------------------------------------------------------------------------


def test_doctor_in_initialized_workspace(initialized_workspace: Path) -> None:
    ws = str(initialized_workspace)
    result = _run(["--workspace", ws, "doctor"])
    # doctor may exit 1 if optional tools (icalBuddy, etc.) are absent,
    # but it must not crash — it must produce output (table goes to stderr when
    # not a TTY), not a traceback
    assert result.returncode in (0, 1), f"unexpected exit {result.returncode}"
    assert "Traceback" not in result.stderr, "doctor must not raise unhandled exception"
    combined = result.stdout + result.stderr
    assert combined.strip(), "doctor must produce output"


# ---------------------------------------------------------------------------
# 12. missing workspace fails gracefully
# ---------------------------------------------------------------------------


def test_missing_workspace_fails_gracefully() -> None:
    result = _run(["--workspace", "/nonexistent", "tracker", "list"])
    assert result.returncode != 0
    # Must emit a human-readable error, not a Python traceback
    assert "Traceback" not in result.stderr
    error_output = result.stdout + result.stderr
    assert error_output.strip(), "must emit some error text"
