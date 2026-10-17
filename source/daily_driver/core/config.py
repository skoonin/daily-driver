from __future__ import annotations

from pathlib import Path

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

# Settings that no longer exist, each with what to do instead. Without this the
# strict schema reports only "Extra inputs are not permitted".
_REMOVED_SETTINGS = {
    "user_profile": "delete it and put your background in context.md.",
    "recurring_tasks": (
        'delete it and list your recurring tasks under "## Recurring Tasks" '
        "in context.md."
    ),
}


class ConfigError(Exception):
    """A .dd-config.yaml that uses settings daily-driver has removed."""


def _validate(path: Path, data: object) -> Config:
    """Validate config data, reporting removed settings with their fix.

    Removed settings are set aside before validation so that any other problem
    in the file shows in the same message, not on the next run.
    """
    if not isinstance(data, dict) or not (
        found := [key for key in _REMOVED_SETTINGS if key in data]
    ):
        return Config.model_validate(data)
    lines = [f"{path} has settings that daily-driver no longer uses:"]
    lines += [f"  - {key}: {_REMOVED_SETTINGS[key]}" for key in found]
    rest = {key: value for key, value in data.items() if key not in found}
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
