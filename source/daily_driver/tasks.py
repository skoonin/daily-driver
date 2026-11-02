"""AI tasks: the named jobs the program hands to a provider.

A leaf module with no daily_driver imports, so core and plugin modules can
both import it without an import cycle. Core tasks are routed by
`ai.providers.<name>.tasks`; a plugin declares its own on `Plugin.tasks`
and routes them under `plugins.<name>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# interactive: a harness the user talks to.
# agent: a harness that reads workspace files on its own, with no user.
# text: prompt in, text out; the program puts all data in the prompt.
Mode = Literal["interactive", "agent", "text"]


@dataclass(frozen=True)
class Task:
    name: str
    mode: Mode


INTERACTIVE = Task("interactive", "interactive")
SUMMARY = Task("summary", "agent")
VOICE_UPDATE = Task("voice_update", "text")

CORE_TASKS: tuple[Task, ...] = (INTERACTIVE, SUMMARY, VOICE_UPDATE)
