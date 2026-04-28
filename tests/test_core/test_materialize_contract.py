"""Regression tests: materialize must produce the file counts codified in the init contract.

These tests exist specifically to catch the broken-wheel gap that slipped into v0.1.0:
test_package_data_resources_are_importable() passed with zero .md files because it only
checked traversable accessibility.  These tests count actual files on disk after a real
materialize(force=True) call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from daily_driver.core import materialize
from daily_driver.core.contract import MIN_AGENTS, MIN_COMMANDS


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

    @classmethod
    def make(cls, root: Path, version: str = "1.0.0") -> _FakeWorkspace:
        state_dir = root / ".daily-driver"
        state_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            state_dir=state_dir,
            version=version,
            logger=logging.getLogger("test.materialize_contract"),
            console=Console(stderr=True),
        )


def test_materialize_produces_minimum_command_count(tmp_path: Path) -> None:
    """materialize(force=True) must copy >= 5 command .md files onto disk.

    The importability test (test_package_data_resources_are_importable) passed
    even when the wheel contained zero .md files — this test closes that gap by
    counting files on disk after an actual materialize run.
    """
    ws = _FakeWorkspace.make(tmp_path)
    materialize.materialize(ws, ignore_drift=True, force_overwrite=True)

    commands_dir = tmp_path / ".claude" / "commands" / "daily-driver"
    assert (
        commands_dir.is_dir()
    ), ".claude/commands/daily-driver must exist after materialize"

    md_files = list(commands_dir.glob("*.md"))
    assert len(md_files) >= MIN_COMMANDS, (
        f".claude/commands/daily-driver must contain >= {MIN_COMMANDS} .md files;"
        f" found {len(md_files)}: {[f.name for f in md_files]}"
    )


def test_materialize_produces_minimum_agent_count(tmp_path: Path) -> None:
    """materialize(force=True) must copy >= 1 agent .md file onto disk."""
    ws = _FakeWorkspace.make(tmp_path)
    materialize.materialize(ws, ignore_drift=True, force_overwrite=True)

    agents_dir = tmp_path / ".claude" / "agents" / "daily-driver"
    assert (
        agents_dir.is_dir()
    ), ".claude/agents/daily-driver must exist after materialize"

    md_files = list(agents_dir.glob("*.md"))
    assert len(md_files) >= MIN_AGENTS, (
        f".claude/agents/daily-driver must contain >= {MIN_AGENTS} .md file(s);"
        f" found {len(md_files)}: {[f.name for f in md_files]}"
    )


def test_materialize_known_command_names_on_disk(tmp_path: Path) -> None:
    """The five named commands must all be present by filename after materialize."""
    ws = _FakeWorkspace.make(tmp_path)
    materialize.materialize(ws, ignore_drift=True, force_overwrite=True)

    commands_dir = tmp_path / ".claude" / "commands" / "daily-driver"
    present = {f.name for f in commands_dir.glob("*.md")}

    expected = {
        "day-start.md",
        "day-end.md",
        "check-in.md",
        "summary.md",
        "voice-update.md",
    }
    missing = expected - present
    assert (
        not missing
    ), f"expected command files missing from .claude/commands/daily-driver/: {missing}"


def test_materialize_known_agent_names_on_disk(tmp_path: Path) -> None:
    """The work-planner agent must be present by filename after materialize."""
    ws = _FakeWorkspace.make(tmp_path)
    materialize.materialize(ws, ignore_drift=True, force_overwrite=True)

    agents_dir = tmp_path / ".claude" / "agents" / "daily-driver"
    present = {f.name for f in agents_dir.glob("*.md")}

    assert (
        "work-planner.md" in present
    ), f"work-planner.md missing from .claude/agents/daily-driver/; found: {present}"
