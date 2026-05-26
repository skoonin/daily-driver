"""Tests for daily_driver.cli.commands.status — dashboard output and stalled detection."""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import pytest
import yaml

from daily_driver.cli.commands.status import run
from daily_driver.core.workspace import Workspace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace.init(tmp_path)


def _args(
    workspace_path: Path | None, *, json_output: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(
        verbose=False,
        quiet=False,
        no_color=False,
        workspace=str(workspace_path) if workspace_path is not None else None,
        json=json_output,
        func=run,
    )


def _patch_tracker_yaml_timestamps(
    tracker_path: Path, entry_id: str, new_updated_at: datetime.datetime
) -> None:
    """Directly rewrite a single entry's updated_at in the tracker YAML."""
    raw = yaml.safe_load(tracker_path.read_text(encoding="utf-8"))
    for entry in raw["entries"]:
        if entry["id"] == entry_id:
            entry["updated_at"] = new_updated_at.isoformat()
            break
    tracker_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. status on empty workspace prints zero totals, exits 0
# ---------------------------------------------------------------------------


def test_status_empty_workspace_prints_zero_totals(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    args = _args(workspace.root)
    result = run(args)

    assert result == 0
    captured = capsys.readouterr()
    # Rich table with "0" for total count must appear somewhere in output
    assert "0" in captured.out


# ---------------------------------------------------------------------------
# 2. status counts entries by category
# ---------------------------------------------------------------------------


def test_status_counts_entries_by_category(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    from daily_driver.core.tracker import Tracker

    tracker = Tracker(workspace)
    tracker.add(category="task", title="Task A")
    tracker.add(category="task", title="Task B")
    tracker.add(category="job", title="Job A")

    args = _args(workspace.root)
    result = run(args)

    assert result == 0
    captured = capsys.readouterr()
    assert "2" in captured.out  # 2 tasks
    assert "1" in captured.out  # 1 job


# ---------------------------------------------------------------------------
# 3. status --json emits parseable JSON with expected keys
# ---------------------------------------------------------------------------


def test_status_json_format(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    from daily_driver.core.tracker import Tracker

    tracker = Tracker(workspace)
    tracker.add(category="task", title="JSON task")

    args = _args(workspace.root, json_output=True)
    result = run(args)

    assert result == 0
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)

    assert envelope["schema"] == 1
    parsed = envelope["data"]
    assert "totals" in parsed
    assert "stalled" in parsed
    assert "recent" in parsed
    assert parsed["totals"]["total"] == 1
    assert parsed["totals"]["by_category"]["task"] == 1


def test_status_setup_gaps_flag_unconfigured_workspace(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    """A fresh `init` workspace must surface the unconfigured signals so it
    looks different from a quiet day with nothing to report."""
    args = _args(workspace.root, json_output=True)
    result = run(args)
    assert result == 0
    envelope = json.loads(capsys.readouterr().out)
    gap_ids = {gap["id"] for gap in envelope["data"]["setup_gaps"]}
    # Fresh workspace: context.md / voice-profile.md absent or templated,
    # tracker is empty, gather.git.search_paths is empty. (The Workspace.init
    # fixture writes only .dd-config.yaml; the CLI `init` command writes the
    # markdown templates. We accept either signal here.)
    assert {"context_unedited", "context_missing"} & gap_ids
    assert {"voice_profile_template", "voice_profile_empty"} & gap_ids
    assert "gather_git_unset" in gap_ids
    assert "tracker_empty" in gap_ids


def test_status_setup_gaps_clear_when_configured(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    """Editing context.md + voice-profile.md + adding a tracker entry +
    setting gather.git.search_paths clears the corresponding gaps."""
    from daily_driver.core.tracker import Tracker

    (workspace.root / "context.md").write_text(
        "# Custom context\nMy actual context.\n", encoding="utf-8"
    )
    (workspace.root / "voice-profile.md").write_text(
        "# Custom voice\nMy actual voice samples.\n", encoding="utf-8"
    )

    config_path = workspace.root / ".dd-config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw.setdefault("gather", {}).setdefault("git", {})["search_paths"] = [
        str(workspace.root)
    ]
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    Tracker(workspace).add(category="task", title="Real task")

    args = _args(workspace.root, json_output=True)
    result = run(args)
    assert result == 0
    envelope = json.loads(capsys.readouterr().out)
    gap_ids = {gap["id"] for gap in envelope["data"]["setup_gaps"]}
    assert "context_unedited" not in gap_ids
    assert "voice_profile_template" not in gap_ids
    assert "voice_profile_empty" not in gap_ids
    assert "gather_git_unset" not in gap_ids
    assert "tracker_empty" not in gap_ids


# ---------------------------------------------------------------------------
# 4. status stalled detection — entries with updated_at >14 days old
#    in non-terminal status are flagged
# ---------------------------------------------------------------------------


def test_status_stalled_detection(
    workspace: Workspace, capsys: pytest.CaptureFixture
) -> None:
    from daily_driver.core.tracker import Tracker

    tracker = Tracker(workspace)
    fresh = tracker.add(category="task", title="Fresh task", status="open")
    stale = tracker.add(category="task", title="Stale task", status="open")
    terminal = tracker.add(category="task", title="Done task", status="done")

    # Backdate stale entry's updated_at to 20 days ago
    now = datetime.datetime.now(tz=datetime.UTC)
    stale_ts = now - datetime.timedelta(days=20)
    # Also backdate terminal entry — it should NOT appear in stalled (terminal status)
    terminal_ts = now - datetime.timedelta(days=30)

    _patch_tracker_yaml_timestamps(tracker.path, stale.id, stale_ts)
    _patch_tracker_yaml_timestamps(tracker.path, terminal.id, terminal_ts)

    args = _args(workspace.root, json_output=True)
    result = run(args)

    assert result == 0
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    parsed = envelope["data"]

    stalled_ids = {e["id"] for e in parsed["stalled"]}
    assert stale.id in stalled_ids, "stale non-terminal entry must be in stalled"
    assert fresh.id not in stalled_ids, "recently-updated entry must not be stalled"
    assert terminal.id not in stalled_ids, "terminal-status entry must not be stalled"
