"""Regression tests: generate must produce the file counts codified in the init contract.

These tests exist specifically to catch the broken-wheel gap that slipped into v0.1.0:
test_package_data_resources_are_importable() passed with zero .md files because it only
checked traversable accessibility.  These tests count actual files on disk after a real
generate(force=True) call.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from rich.console import Console

from daily_driver.core import generate
from daily_driver.core.contract import MIN_AGENTS, MIN_COMMANDS


@dataclass
class _FakeWorkspace:
    root: Path
    state_dir: Path
    version: str
    logger: logging.Logger
    console: Console
    config: object = None  # no plugins block, so every plugin is off

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
            logger=logging.getLogger("test.generate_contract"),
            console=Console(stderr=True),
        )


def test_generate_produces_minimum_command_count(tmp_path: Path) -> None:
    """generate(force=True) must copy >= 5 command .md files onto disk.

    The importability test (test_package_data_resources_are_importable) passed
    even when the wheel contained zero .md files — this test closes that gap by
    counting files on disk after an actual generate run.
    """
    ws = _FakeWorkspace.make(tmp_path)
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    commands_dir = tmp_path / ".claude" / "commands" / "daily-driver"
    assert (
        commands_dir.is_dir()
    ), ".claude/commands/daily-driver must exist after generate"

    md_files = list(commands_dir.glob("*.md"))
    assert len(md_files) >= MIN_COMMANDS, (
        f".claude/commands/daily-driver must contain >= {MIN_COMMANDS} .md files;"
        f" found {len(md_files)}: {[f.name for f in md_files]}"
    )


def test_generate_produces_minimum_agent_count(tmp_path: Path) -> None:
    """generate(force=True) must copy >= 1 agent .md file onto disk."""
    ws = _FakeWorkspace.make(tmp_path)
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    agents_dir = tmp_path / ".claude" / "agents" / "daily-driver"
    assert agents_dir.is_dir(), ".claude/agents/daily-driver must exist after generate"

    md_files = list(agents_dir.glob("*.md"))
    assert len(md_files) >= MIN_AGENTS, (
        f".claude/agents/daily-driver must contain >= {MIN_AGENTS} .md file(s);"
        f" found {len(md_files)}: {[f.name for f in md_files]}"
    )


def test_generate_known_command_names_on_disk(tmp_path: Path) -> None:
    """The five named commands must all be present by filename after generate."""
    ws = _FakeWorkspace.make(tmp_path)
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    commands_dir = tmp_path / ".claude" / "commands" / "daily-driver"
    present = {f.name for f in commands_dir.glob("*.md")}

    expected = {
        "day-start.md",
        "day-end.md",
        "check-in.md",
        "summary.md",
        "voice-update.md",
    }
    assert present == expected


def test_generate_known_agent_names_on_disk(tmp_path: Path) -> None:
    """The work-planner agent must be present by filename after generate."""
    ws = _FakeWorkspace.make(tmp_path)
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    agents_dir = tmp_path / ".claude" / "agents" / "daily-driver"
    present = {f.name for f in agents_dir.glob("*.md")}

    assert (
        "work-planner.md" in present
    ), f"work-planner.md missing from .claude/agents/daily-driver/; found: {present}"


def test_workspace_readme_written_on_init(tmp_path: Path) -> None:
    """generate(force_overwrite=True) must write README.md to the workspace root."""
    ws = _FakeWorkspace.make(tmp_path)
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    readme = tmp_path / "README.md"
    assert readme.exists(), "README.md must exist in workspace root after generate"
    assert "daily-driver" in readme.read_text(
        encoding="utf-8"
    ), "README.md must contain 'daily-driver'"


# opencode runs `!`cmd`` blocks, swaps `$ARGUMENTS` and `$1`.., and inlines
# `@path` files, so a shipped command must use none of them by accident.
_OPENCODE_SPECIAL = re.compile(r"!`|\$ARGUMENTS|\$\d|(?:^|\s)@[\w./~-]")


def test_generate_writes_each_command_for_opencode(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    claude_dir = tmp_path / ".claude" / "commands" / "daily-driver"
    opencode_dir = tmp_path / ".opencode" / "commands" / "daily-driver"
    assert {f.name for f in opencode_dir.glob("*.md")} == {
        f.name for f in claude_dir.glob("*.md")
    }

    for path in opencode_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = text.removeprefix("---\n").split("\n---\n", 1)
        assert set(yaml.safe_load(frontmatter)) == {"description"}, path.name
        for token in ("{%", "{{", "/daily-driver:", "~/.claude", "Claude Read tool"):
            assert token not in body, f"{path.name} has {token!r}"
        assert not _OPENCODE_SPECIAL.search(body), path.name


def test_generate_gives_opencode_session_commands_the_persona(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    opencode_dir = tmp_path / ".opencode" / "commands" / "daily-driver"
    for name in ("day-start", "day-end", "check-in"):
        text = (opencode_dir / f"{name}.md").read_text(encoding="utf-8")
        assert "# Role: work-planner" in text, name
    summary = (opencode_dir / "summary.md").read_text(encoding="utf-8")
    assert "# Role:" not in summary


def test_contract_needs_the_opencode_commands(tmp_path: Path) -> None:
    from daily_driver.core import contract

    ws = _FakeWorkspace.make(tmp_path)
    generate.generate(ws, ignore_drift=True, force_overwrite=True)
    for path in (tmp_path / ".opencode" / "commands" / "daily-driver").glob("*.md"):
        path.unlink()

    violations = contract.check(tmp_path)

    assert ".opencode/commands/daily-driver" in {v.rel_path for v in violations}
