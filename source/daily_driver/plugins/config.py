"""PluginsConfig assembled from PLUGINS metadata.

Lives in the plugins package (not core) so the root Config can stay free of
hardcoded plugin namespaces: each plugin contributes its own typed field via
``Plugin.config_model``. Core imports ``PluginsConfig`` from here; the import
runs one direction only (plugins never import core), so there is no cycle.

``extra="forbid"``: every registered plugin already gets a typed field built
from ``PLUGINS`` below, so an unrecognized ``plugins.<name>`` key is a typo or
an unregistered plugin and should fail loudly — matching the strict root
(``Config`` is ``extra="forbid"``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from daily_driver.plugins import PLUGINS


class _PluginsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _present_block_is_on(cls, data: Any) -> Any:
        # `job_search:` with every option commented out loads as null. The
        # block is there, so the plugin is on with default settings; only an
        # absent key (or `enabled: false`) leaves it off.
        if isinstance(data, dict):
            return {key: {} if value is None else value for key, value in data.items()}
        return data


_plugin_fields: dict[str, Any] = {}
for _plugin in PLUGINS:
    if _plugin.config_model is None:
        continue
    _plugin_fields[_plugin.name] = (
        _plugin.config_model | None,
        Field(default=None, description=""),
    )

PluginsConfig: type[BaseModel] = create_model(
    "PluginsConfig",
    __base__=_PluginsBase,
    **_plugin_fields,
)
