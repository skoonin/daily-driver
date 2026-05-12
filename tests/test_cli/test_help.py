"""Tests for `daily-driver help` reference command.

The `help` command is a content-rich discovery surface (commands + values),
distinct from argparse's `--help` (which prints usage). It supports topic
filtering and `--json` for scripting.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from daily_driver.cli.cli import app
from daily_driver.core.workspace import Workspace


def _capture_stdout(fn) -> str:
    buf = io.StringIO()
    orig = sys.stdout
    sys.stdout = buf
    try:
        fn()
    finally:
        sys.stdout = orig
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_help_is_top_level_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """`daily-driver --help` must list `help` so users can discover it."""
    with pytest.raises(SystemExit):
        app(["--help"])
    out = capsys.readouterr().out
    assert "help" in out


# ---------------------------------------------------------------------------
# Full reference
# ---------------------------------------------------------------------------


def test_help_full_no_args_prints_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help"])
    assert rc == 0
    out = capsys.readouterr().out
    # Each value group must appear, with its consumer flagged for context.
    for section in (
        "commands",
        "statuses",
        "categories",
        "sources",
        "dates",
        "cadences",
    ):
        assert section in out.lower()


def test_help_full_json_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    data = payload["data"]
    for key in ("commands", "statuses", "categories", "sources", "dates", "cadences"):
        assert key in data, f"missing top-level key: {key}"


# ---------------------------------------------------------------------------
# Topic filtering
# ---------------------------------------------------------------------------


def test_help_statuses_lists_recommended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help", "statuses"])
    assert rc == 0
    out = capsys.readouterr().out
    for s in ("open", "in-progress", "blocked", "done", "ruled-out"):
        assert s in out


def test_help_statuses_includes_in_use_when_workspace_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """In-use custom statuses (from tracker.yaml) appear alongside recommended."""
    ws = Workspace.init(tmp_path)
    from daily_driver.core.tracker import Tracker

    tracker = Tracker(ws)
    tracker.add(category="task", title="t1", status="needs-review")
    capsys.readouterr()  # discard add warning

    rc = app(["--workspace", str(tmp_path), "help", "statuses", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert "needs-review" in data["statuses"]["in_use"]
    assert "open" in data["statuses"]["recommended"]


def test_help_categories_uses_workspace_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help", "categories", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    # Default-init scaffolds a `task` category; the help command must surface it.
    assert "task" in data["categories"]["categories"]
    assert data["categories"]["default"] == "task"


def test_help_sources_lists_known_scrapers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help", "sources", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    # SOURCE_REGISTRY ships at least these.
    for src in ("remoteok", "weworkremotely", "greenhouse", "jobspy"):
        assert src in data["sources"]["sources"]


def test_help_dates_lists_grammar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help", "dates", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    tokens = data["dates"]["tokens"]
    for t in ("today", "yesterday", "week", "month", "year", "Nd", "YYYY-MM-DD"):
        assert t in tokens


def test_help_cadences_lists_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help", "cadences", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert set(data["cadences"]) >= {"daily", "weekly", "monthly"}


def test_help_commands_lists_top_level_subcommands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help", "commands", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    names = {entry["name"] for entry in data["commands"]}
    # A representative slice; we don't pin the full set here.
    assert {"init", "tracker", "doctor", "status", "focus", "jobs", "help"} <= names


def test_help_commands_includes_jobs_search_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    rc = app(["--workspace", str(tmp_path), "help", "commands", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    jobs_entry = next(item for item in data["commands"] if item["name"] == "jobs")
    summary = jobs_entry["summary"].lower()
    assert "job" in summary
    assert "search" in summary or "workflow" in summary


# ---------------------------------------------------------------------------
# Without a workspace
# ---------------------------------------------------------------------------


def test_help_without_workspace_does_not_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`help` is a discovery surface — must not require a workspace."""
    no_ws = tmp_path / "empty"
    no_ws.mkdir()
    rc = app(["--workspace", str(no_ws), "help"])
    assert rc == 0


def test_help_statuses_without_workspace_recommended_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    no_ws = tmp_path / "empty"
    no_ws.mkdir()
    rc = app(["--workspace", str(no_ws), "help", "statuses", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert "open" in data["statuses"]["recommended"]
    assert data["statuses"]["in_use"] == []


def test_help_categories_without_workspace_returns_empty_with_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    no_ws = tmp_path / "empty"
    no_ws.mkdir()
    rc = app(["--workspace", str(no_ws), "help", "categories"])
    assert rc == 0
    out = capsys.readouterr().out
    # Hint should point users at the config file rather than crashing.
    assert "no workspace" in out.lower() or "configure" in out.lower()


# ---------------------------------------------------------------------------
# Bad topic
# ---------------------------------------------------------------------------


def test_help_unknown_topic_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """argparse rejects an unknown choice with exit 2."""
    with pytest.raises(SystemExit) as exc:
        app(["help", "bogus-topic"])
    assert exc.value.code == 2
