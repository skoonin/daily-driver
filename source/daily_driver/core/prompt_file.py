"""Prompts for a harness that reads no `.claude/` files.

Claude Code reads the workspace's generated `.claude/` commands and agents. Any
other harness gets the same instructions in two forms, each holding the agent
persona (when the agent is a shipped one) followed by the instructions,
rendered with `claude_code=False` so Claude-only steps are left out:

- the prompt file, through the `{prompt_file}` placeholder of a provider's
  `run` or `resume` template;
- an opencode command file, which `generate` writes under
  `.opencode/commands/` so the commands work in an opencode session.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import yaml

from daily_driver.core.templating import render_text
from daily_driver.core.workspace import Workspace

# Shipped prompts live in a `daily-driver` folder inside each package; the
# hyphen makes it a plain directory, reached with joinpath.
_COMMANDS_PACKAGE = "daily_driver.resources.slash_commands"
_AGENTS_PACKAGE = "daily_driver.resources.agents"
_SHIPPED_DIR = "daily-driver"

# The agent the session launchers load by default (`--agent`).
_SESSION_AGENT = "work-planner"
# Commands that a launcher starts as a session under _SESSION_AGENT. Claude Code
# loads that agent with `--agent`; an opencode command carries it inline.
_SESSION_COMMANDS = frozenset({"day-start", "day-end", "check-in"})


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """The YAML frontmatter (or None) and the body after it."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + len("\n---\n") :].lstrip("\n")


def _shipped(package: str, name: str) -> str | None:
    source = importlib.resources.files(package).joinpath(_SHIPPED_DIR, f"{name}.md")
    return source.read_text(encoding="utf-8") if source.is_file() else None


def _render_prompt(
    config: object, *, name: str, instructions: str, agent: str | None
) -> str:
    """The persona of `agent` (when shipped), then the instructions, rendered."""
    persona = _shipped(_AGENTS_PACKAGE, agent) if agent else None
    # Headings keep the instructions from reading as the persona's last section.
    sections = [(f"# Role: {agent}", persona), (f"# Session: {name}", instructions)]
    parts = [
        f"{heading}\n\n"
        + render_text(_split_frontmatter(text)[1], config, claude_code=False)
        for heading, text in sections
        if text is not None
    ]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def write_prompt_file(workspace: Workspace, *, command: str, agent: str | None) -> Path:
    """Write the prompt for `command` (e.g. `/daily-driver:day-start`); return its path."""
    name = command.rpartition(":")[2].lstrip("/")
    instructions = _shipped(_COMMANDS_PACKAGE, name)
    if instructions is None:
        raise ValueError(f"no shipped instructions for {command!r}")
    path = workspace.ephemeral_dir / "prompts" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_prompt(
            workspace.config, name=name, instructions=instructions, agent=agent
        ),
        encoding="utf-8",
    )
    return path


def opencode_command(config: object, *, name: str, text: str) -> str:
    """A shipped command as an opencode command file.

    opencode reads only `description` from the frontmatter (`name` would be an
    unknown field), and runs the body as the prompt. A session command carries
    the _SESSION_AGENT persona, because an opencode session opened by hand has
    no agent loaded.
    """
    frontmatter, _ = _split_frontmatter(text)
    meta = yaml.safe_load(frontmatter) if frontmatter else None
    description = meta.get("description") if isinstance(meta, dict) else None
    agent = _SESSION_AGENT if name in _SESSION_COMMANDS else None
    body = _render_prompt(config, name=name, instructions=text, agent=agent)
    if description is None:
        return body
    header = yaml.safe_dump(
        {"description": description}, allow_unicode=True, sort_keys=False, width=1000
    )
    return f"---\n{header}---\n\n{body}"
