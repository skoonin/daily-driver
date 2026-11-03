"""The prompt file: one launcher's instructions for a harness without .claude/.

Claude Code reads the workspace's generated `.claude/` commands and agents; any
other harness gets this file instead, through the `{prompt_file}` placeholder
of its `run` or `resume` template. It holds the agent persona (when the agent
is a shipped one) followed by the launcher's instructions, rendered with
`claude_code=False` so Claude-only steps are left out.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from daily_driver.core.templating import render_text
from daily_driver.core.workspace import Workspace

# Shipped prompts live in a `daily-driver` folder inside each package; the
# hyphen makes it a plain directory, reached with joinpath.
_COMMANDS_PACKAGE = "daily_driver.resources.slash_commands"
_AGENTS_PACKAGE = "daily_driver.resources.agents"
_SHIPPED_DIR = "daily-driver"


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text if end == -1 else text[end + len("\n---\n") :].lstrip("\n")


def _shipped(package: str, name: str) -> str | None:
    source = importlib.resources.files(package).joinpath(_SHIPPED_DIR, f"{name}.md")
    return source.read_text(encoding="utf-8") if source.is_file() else None


def write_prompt_file(workspace: Workspace, *, command: str, agent: str | None) -> Path:
    """Write the prompt for `command` (e.g. `/daily-driver:day-start`); return its path."""
    name = command.rpartition(":")[2].lstrip("/")
    instructions = _shipped(_COMMANDS_PACKAGE, name)
    if instructions is None:
        raise ValueError(f"no shipped instructions for {command!r}")
    persona = _shipped(_AGENTS_PACKAGE, agent) if agent else None
    # Headings keep the instructions from reading as the persona's last section.
    sections = [(f"# Role: {agent}", persona), (f"# Session: {name}", instructions)]
    parts = [
        f"{heading}\n\n"
        + render_text(_strip_frontmatter(text), workspace.config, claude_code=False)
        for heading, text in sections
        if text is not None
    ]
    path = workspace.ephemeral_dir / "prompts" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n\n".join(part.strip() for part in parts) + "\n", encoding="utf-8"
    )
    return path
