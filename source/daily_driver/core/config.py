from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from daily_driver.core.config_models import (
    Config,
    TrackerCategoryConfig,
    TrackerConfig,
)
from daily_driver.core.console import Console
from daily_driver.core.logging import get_logger
from daily_driver.plugins import PLUGINS, enabled_plugins
from daily_driver.plugins._base import Plugin

_logger = get_logger("config")

# Settings that no longer exist, each with what to do instead, by dotted path.
# The load ignores them and warns with this text.
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
    "daily_driver": (
        "delete it and the indented lines under it; data files are always in "
        "the workspace folder."
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
}

# A plugin's removed settings, with the plugin that owns each. While that
# plugin is off, the warning only says to delete the setting: its own fix
# would name the plugin's settings, and adding them switches the plugin on.
_PLUGIN_REMOVED_SETTINGS = [
    (plugin, dotted, fix)
    for plugin in PLUGINS
    for dotted, fix in plugin.removed_settings.items()
]
_DELETE_FIX = "delete it and any lines indented under it."


_UNKNOWN_SETTING_FIX = "no setting has this name; correct the spelling or delete it."

# Ignoring this would point every command at an empty workspace folder and
# leave the user's data behind, so it stops the load.
_MOVED_DATA_FIX = (
    "daily_driver.output_dir: data files are now always in the workspace folder. "
    "Move the files from that folder into it, then delete the section and the "
    "indented lines under it."
)

# Commands load the config more than once; each setting warns once.
_warned: set[tuple[Path, str]] = set()


class ConfigError(Exception):
    """A .dd-config.yaml that daily-driver cannot safely run with."""


@dataclass(frozen=True)
class IgnoredSetting:
    """A setting the load left out, and what to do about it."""

    path: str
    fix: str


def _parent(data: object, dotted: str) -> tuple[dict[str, Any] | None, str]:
    """The mapping holding the last key of `dotted`, and that key."""
    *parents, key = dotted.split(".")
    node: object = data
    for part in parents:
        node = node.get(part) if isinstance(node, dict) else None
    return (node if isinstance(node, dict) else None), key


def _moves_data(data: object) -> bool:
    """Whether the config kept data files outside the workspace folder."""
    holder, key = _parent(data, "daily_driver.output_dir")
    return holder is not None and holder.get(key) not in (None, ".", "./")


def _drop_unknown(data: object, loc: tuple[int | str, ...]) -> str | None:
    """Delete the key at a validation error's `loc`; its dotted path, if found."""
    node: Any = data
    for part in loc[:-1]:
        try:
            node = node[part]
        except (KeyError, IndexError, TypeError):
            return None
    if not isinstance(node, dict) or loc[-1] not in node:
        return None
    del node[loc[-1]]
    return ".".join(str(part) for part in loc)


def _validate(data: object) -> tuple[Config, list[IgnoredSetting]]:
    """Validate config data, leaving out removed and unknown settings.

    A value of the wrong type is still an error: there is no value to fall back
    on that the user asked for.
    """
    rest = copy.deepcopy(data)
    removed: list[tuple[Plugin | None, str, str]] = [
        *((None, dotted, fix) for dotted, fix in _REMOVED_SETTINGS.items()),
        *_PLUGIN_REMOVED_SETTINGS,
    ]
    found_removed = []
    for owner, dotted, fix in removed:
        holder, key = _parent(rest, dotted)
        if holder is not None and key in holder:
            del holder[key]
            found_removed.append((owner, dotted, fix))
    unknown: list[IgnoredSetting] = []
    while True:
        try:
            config = Config.model_validate(rest)
        except ValidationError as exc:
            dropped = [
                _drop_unknown(rest, error["loc"])
                for error in exc.errors()
                if error["type"] == "extra_forbidden"
            ]
            found = [IgnoredSetting(p, _UNKNOWN_SETTING_FIX) for p in dropped if p]
            if not found:
                raise
            unknown.extend(found)
            continue
        on = enabled_plugins(config)
        ignored = [
            IgnoredSetting(dotted, fix if owner is None or owner in on else _DELETE_FIX)
            for owner, dotted, fix in found_removed
        ]
        return config, ignored + unknown


def _where(exc: yaml.YAMLError, raw: str) -> str:
    """Where in the file a YAML error is, and what it is.

    PyYAML's own text names the input `<unicode string>`, because the file is
    read before it is parsed. A forbidden character raises a ReaderError, which
    has a `reason` and a character offset into `raw` instead of a line mark.
    """
    mark = getattr(exc, "problem_mark", None)
    if mark:
        at = f" at line {mark.line + 1}, column {mark.column + 1}"
    elif isinstance(exc, yaml.reader.ReaderError):
        # Everything before the offset passed YAML's printable check, so the
        # only breaks splitlines() can find there are YAML's own line breaks.
        # The appended "x" stands in for the bad character, so a line it
        # starts still counts. PyYAML's own marks give a byte order mark no
        # column, so it gets none here either.
        lines = (raw[: exc.position] + "x").splitlines()
        column = len(lines[-1]) - lines[-1].count("\ufeff")
        at = f" at line {len(lines)}, column {column}"
    else:
        at = ""
    problem = getattr(exc, "problem", None) or getattr(exc, "reason", None)
    return f"{at}: {problem}" if problem else at


def read_settings(path: Path) -> tuple[Config, list[IgnoredSetting]]:
    """Read a .dd-config.yaml without printing: the config and what it ignored."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path} cannot be read: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML{_where(exc, raw)}") from exc

    if not data:
        config = Config(
            tracker=TrackerConfig(categories={"task": TrackerCategoryConfig()}),
        )
        return config, []

    if _moves_data(data):
        raise ConfigError(f"{path} needs an edit:\n  - {_MOVED_DATA_FIX}")

    _logger.debug("loaded config from %s", path)
    return _validate(data)


def load(path: Path) -> Config:
    """Load and validate a .dd-config.yaml, warning about ignored settings."""
    config, ignored = read_settings(path)
    for setting in ignored:
        if (path, setting.path) not in _warned:
            _warned.add((path, setting.path))
            Console.warning(f"{path}: ignoring {setting.path}: {setting.fix}")
    return config
