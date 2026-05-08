"""Tests for daily_driver.cli.commands.tracker — argparse dispatch and output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from daily_driver.cli.commands.tracker import run
from daily_driver.core.workspace import Workspace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace.init(tmp_path)


def _args(**kwargs: Any) -> argparse.Namespace:
    """Build an argparse Namespace with CLI-global defaults plus overrides."""
    defaults: dict[str, Any] = {
        "verbose": False,
        "quiet": False,
        "no_color": False,
        "workspace": None,
        # tracker_action and func are set per-test
        "tracker_action": None,
        "func": run,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _add_args(workspace_path: Path, **kwargs: Any) -> argparse.Namespace:
    """Namespace pre-wired for tracker add."""
    from daily_driver.cli.commands.tracker import _run_add

    return _args(
        func=_run_add,
        tracker_action="add",
        workspace=str(workspace_path),
        category=kwargs.get("category", "task"),
        title=kwargs.get("title", "Test entry"),
        status=kwargs.get("status", None),
        tags=kwargs.get("tags", None),
        link=kwargs.get("link", None),
        note=kwargs.get("note", None),
        next_action=kwargs.get("next_action", None),
        due=kwargs.get("due", None),
        extra=kwargs.get("extra", None),
    )


def _list_args(workspace_path: Path, **kwargs: Any) -> argparse.Namespace:
    """Namespace pre-wired for tracker list."""
    from daily_driver.cli.commands.tracker import _run_list

    return _args(
        func=_run_list,
        tracker_action="list",
        workspace=str(workspace_path),
        category=kwargs.get("category", None),
        status=kwargs.get("status", None),
        tag=kwargs.get("tag", None),
        since=kwargs.get("since", None),
        json=kwargs.get("json", False),
    )


def _update_args(
    workspace_path: Path, entry_id: str, **kwargs: Any
) -> argparse.Namespace:
    """Namespace pre-wired for tracker update."""
    from daily_driver.cli.commands.tracker import _run_update

    return _args(
        func=_run_update,
        tracker_action="update",
        workspace=str(workspace_path),
        id=entry_id,
        status=kwargs.get("status", None),
        note=kwargs.get("note", None),
        next_action=kwargs.get("next_action", None),
        tags=kwargs.get("tags", None),
        extra=kwargs.get("extra", None),
    )


# ---------------------------------------------------------------------------
# 1. tracker add creates entry and exits zero
# ---------------------------------------------------------------------------


def test_add_creates_entry_and_exits_zero(workspace: Workspace) -> None:
    args = _add_args(workspace.root, title="My task", category="task")
    result = run(args)

    assert result == 0

    from daily_driver.core.tracker import Tracker

    tracker = Tracker(workspace)
    entries = tracker.list(category="task")
    assert len(entries) == 1
    assert entries[0].title == "My task"
    assert entries[0].id == "task-001"


# ---------------------------------------------------------------------------
# 2. tracker add missing --category exits 2 (argparse catches before run)
# ---------------------------------------------------------------------------


def test_add_missing_category_exits_2() -> None:
    """argparse requires --category; missing it causes SystemExit(2)."""
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc_info:
        # Redirect stderr to suppress argparse error message in test output
        app(["tracker", "add", "--title", "No category"])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 3. tracker list table output contains entry titles
# ---------------------------------------------------------------------------


def test_list_table_output_contains_titles(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    from daily_driver.core.tracker import Tracker

    tracker = Tracker(workspace)
    tracker.add(category="task", title="Write unit tests")
    tracker.add(category="task", title="Review PR")

    args = _list_args(workspace.root)
    result = run(args)

    assert result == 0
    captured = capsys.readouterr()
    assert "Write unit tests" in captured.out
    assert "Review PR" in captured.out


# ---------------------------------------------------------------------------
# 4. tracker list --json output parses as a list of dicts
# ---------------------------------------------------------------------------


def test_list_json_output_parses_as_json(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    from daily_driver.core.tracker import Tracker

    tracker = Tracker(workspace)
    tracker.add(category="task", title="JSON task")

    args = _list_args(workspace.root, json=True)
    result = run(args)

    assert result == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["schema"] == 1
    entries = parsed["data"]["entries"]
    assert isinstance(entries, list)
    assert len(entries) == 1
    assert entries[0]["title"] == "JSON task"
    assert entries[0]["category"] == "task"


# ---------------------------------------------------------------------------
# 5. tracker update unknown ID exits 1
# ---------------------------------------------------------------------------


def test_update_unknown_id_exits_1(workspace: Workspace) -> None:
    args = _update_args(workspace.root, "task-999", status="done")
    result = run(args)
    assert result == 1


# ---------------------------------------------------------------------------
# 6. tracker without workspace exits 1
# ---------------------------------------------------------------------------


def test_tracker_without_workspace_exits_1(tmp_path: Path) -> None:
    """CWD outside a workspace, no --workspace flag → exit 1."""
    # Pass a workspace path pointing to a dir with no .dd-config.yaml
    no_ws_path = tmp_path / "not-a-workspace"
    no_ws_path.mkdir()
    args = _add_args(no_ws_path, title="Will fail")
    # Override workspace so it points to a path with no marker
    args.workspace = str(no_ws_path)
    result = run(args)
    assert result == 1


# ---------------------------------------------------------------------------
# 7. --extra KEY=VALUE pairs produce extras dict
# ---------------------------------------------------------------------------


def test_extra_key_value_parsing(workspace: Workspace) -> None:
    args = _add_args(
        workspace.root,
        title="Job with extras",
        category="task",
        extra=["comp=200", "source=linkedin"],
    )
    result = run(args)
    assert result == 0

    from daily_driver.core.tracker import Tracker

    tracker = Tracker(workspace)
    entries = tracker.list(category="task")
    assert len(entries) == 1
    assert entries[0].extras == {"comp": "200", "source": "linkedin"}


# ---------------------------------------------------------------------------
# 8. tracker update --note appends (does not replace)
# ---------------------------------------------------------------------------


def test_update_note_appends_not_replaces(workspace: Workspace) -> None:
    """Two successive --note updates must produce both strings joined by newline."""
    from daily_driver.core.tracker import Tracker

    # Add entry with no initial note.
    add_args = _add_args(workspace.root, title="Append test", category="task")
    assert run(add_args) == 0

    tracker = Tracker(workspace)
    entries = tracker.list(category="task")
    entry_id = entries[0].id

    # First note update.
    upd1 = _update_args(workspace.root, entry_id, note="first note")
    assert run(upd1) == 0

    # Second note update — must append, not replace.
    upd2 = _update_args(workspace.root, entry_id, note="second note")
    assert run(upd2) == 0

    updated = Tracker(workspace).list(category="task")[0]
    assert updated.notes == "first note\nsecond note"


# ---------------------------------------------------------------------------
# 9. tracker list --since SPEC filters by updated_at
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected_titles",
    [
        ("today", {"recent"}),
        ("7d", {"recent", "five-days-ago"}),
        ("month", {"recent", "five-days-ago"}),
    ],
)
def test_list_since_filters_by_updated_at(
    workspace: Workspace, spec: str, expected_titles: set[str]
) -> None:
    """--since uses parse_since grammar; filters on updated_at >= parsed date."""
    from datetime import datetime, timedelta, timezone

    from daily_driver.core.tracker import Tracker

    tracker = Tracker(workspace)
    tracker.add(category="task", title="recent")
    tracker.add(category="task", title="five-days-ago")
    tracker.add(category="task", title="long-ago")

    # Backdate two entries by mutating the YAML directly via tracker.save.
    state = tracker.load()
    now = datetime.now(timezone.utc)
    for entry in state.entries:
        if entry.title == "five-days-ago":
            entry.updated_at = now - timedelta(days=5)
        elif entry.title == "long-ago":
            entry.updated_at = now - timedelta(days=120)
    tracker.save(state)

    args = _list_args(workspace.root, since=spec, json=True)
    import io
    import sys as _sys

    buf = io.StringIO()
    _sys.stdout, orig = buf, _sys.stdout
    try:
        rc = run(args)
    finally:
        _sys.stdout = orig

    assert rc == 0
    parsed = json.loads(buf.getvalue())
    titles = {e["title"] for e in parsed["data"]["entries"]}
    assert titles == expected_titles


def test_list_since_invalid_spec_exits_2(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    args = _list_args(workspace.root, since="not-a-spec")
    assert run(args) == 2
    assert "invalid date spec" in capsys.readouterr().err
