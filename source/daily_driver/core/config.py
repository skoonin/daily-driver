from __future__ import annotations

from pathlib import Path

import yaml

from daily_driver.core.config_models import (
    Config,
    DailyDriverConfig,
    TrackerCategoryConfig,
    TrackerConfig,
)
from daily_driver.core.logging import get_logger

_logger = get_logger("config")


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
    return Config.model_validate(data)
