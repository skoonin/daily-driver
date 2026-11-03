"""The prompt file: launcher instructions for harnesses that read no .claude/ files."""

from __future__ import annotations

from pathlib import Path

from daily_driver.core.prompt_file import write_prompt_file
from daily_driver.core.workspace import Workspace


def _workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace.init(root)


def test_prompt_file_holds_persona_then_instructions(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    path = write_prompt_file(
        workspace, command="/daily-driver:day-start", agent="work-planner"
    )

    assert path == workspace.ephemeral_dir / "prompts" / "day-start.md"
    text = path.read_text(encoding="utf-8")
    assert text.index("daily-driver planning assistant") < text.index(
        "Morning planning session"
    )


def test_prompt_file_strips_frontmatter_and_claude_only_lines(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    text = write_prompt_file(
        workspace, command="/daily-driver:check-in", agent="work-planner"
    ).read_text(encoding="utf-8")

    assert not text.startswith("---")
    assert "name: check-in" not in text
    for token in ("~/.claude", "Claude Read tool", "/daily-driver:", "{%"):
        assert token not in text
    assert "`daily-driver day-start`" in text


def test_unknown_agent_gives_instructions_only(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    text = write_prompt_file(
        workspace, command="/daily-driver:day-end", agent="my-own-agent"
    ).read_text(encoding="utf-8")

    assert text.startswith("# Session: day-end\n\nEnd-of-day review session.")
    assert "daily-driver planning assistant" not in text
