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

from pydantic import BaseModel, ConfigDict, Field, create_model

from daily_driver.plugins import PLUGINS

_plugin_fields: dict[str, Any] = {}
for _plugin in PLUGINS:
    if _plugin.config_model is None:
        continue
    _plugin_fields[_plugin.name] = (
        _plugin.config_model | None,
        Field(
            default=None,
            description="",
            json_schema_extra={"template_example_model": True},
        ),
    )

PluginsConfig: type[BaseModel] = create_model(
    "PluginsConfig",
    __config__=ConfigDict(extra="forbid"),
    **_plugin_fields,
)
