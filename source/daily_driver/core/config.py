from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from daily_driver.core.config_models import (
    Config,
    DailyDriverConfig,
    TrackerCategoryConfig,
    TrackerConfig,
)
from daily_driver.core.logging import get_logger

_logger = get_logger("config")

# Settings that no longer exist, each with what to do instead, by dotted path.
# Without this the strict schema reports only "Extra inputs are not permitted".
_TASKS_MOVED = (
    "delete it and the indented lines under it; a provider under ai.providers "
    "now claims the tasks it runs, for example `opencode: {tasks: [summary]}`, "
    "and a model pin moves to that provider's `model`, for example "
    "ai.providers.claude.model."
)
_REMOVED_SETTINGS = {
    "user_profile": (
        "delete it and the indented lines under it, and put your background in "
        "context.md."
    ),
    "recurring_tasks": (
        "delete it and the indented lines under it, and list your recurring "
        'tasks under "## Recurring Tasks" in context.md.'
    ),
    "claude": (
        "delete it and the indented lines under it; claude.resume_check_in is "
        "now ai.interactive.resume_check_in."
    ),
    "ai.provider": _TASKS_MOVED,
    "ai.summary": _TASKS_MOVED,
    "ai.voice_update": _TASKS_MOVED,
    "ai.model": (
        "delete it and the indented lines under it; set `model` on a provider "
        "under ai.providers."
    ),
    "ai.interactive.model": (
        "delete it and the indented lines under it; set `model` on the provider "
        "that runs interactive under ai.providers."
    ),
    "ai.claude": (
        "delete it and the indented lines under it; set "
        "ai.providers.claude.max_parallel instead."
    ),
    "ai.ollama": (
        "delete it and the indented lines under it; ollama is a built-in "
        "provider at http://localhost:11434/v1, so set ai.providers.ollama.model "
        "and its tasks."
    ),
    **{
        f"plugins.job_search.enrichment.{key}": (
            "delete it and the indented lines under it; set "
            "plugins.job_search.provider, or plugins.job_search.tasks.fit_notes, "
            "to a provider under ai.providers."
        )
        for key in ("provider", "model", "fit_notes")
    },
}


class ConfigError(Exception):
    """A .dd-config.yaml that uses settings daily-driver has removed."""


def _parent(data: object, dotted: str) -> tuple[dict[str, Any] | None, str]:
    """The mapping holding the last key of `dotted`, and that key."""
    *parents, key = dotted.split(".")
    node: object = data
    for part in parents:
        node = node.get(part) if isinstance(node, dict) else None
    return (node if isinstance(node, dict) else None), key


def _validate(path: Path, data: object) -> Config:
    """Validate config data, reporting removed settings with their fix.

    Removed settings are set aside before validation so that any other problem
    in the file shows in the same message, not on the next run.
    """
    found = []
    for dotted in _REMOVED_SETTINGS:
        holder, key = _parent(data, dotted)
        if holder is not None and key in holder:
            found.append(dotted)
    if not found:
        return Config.model_validate(data)
    lines = [f"{path} has settings that daily-driver no longer uses:"]
    lines += [f"  - {dotted}: {_REMOVED_SETTINGS[dotted]}" for dotted in found]
    rest = copy.deepcopy(data)
    for dotted in found:
        holder, key = _parent(rest, dotted)
        if holder is not None:
            holder.pop(key, None)
    try:
        Config.model_validate(rest)
    except ValidationError as exc:
        lines += ["It also has these problems:", str(exc)]
    raise ConfigError("\n".join(lines))


def load(path: Path) -> Config:
    """Load and validate a .dd-config.yaml file, returning a Config instance."""
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)

    if not data:
        return Config(
            daily_driver=DailyDriverConfig(),
            tracker=TrackerConfig(categories={"task": TrackerCategoryConfig()}),
        )

    _logger.debug("loaded config from %s", path)
    return _validate(path, data)
