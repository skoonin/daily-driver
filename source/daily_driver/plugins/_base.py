from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class Plugin:
    """Static description of a daily-driver plugin.

    Plugins are listed explicitly in plugins.PLUGINS (no runtime discovery).
    Core wires a plugin's CLI command from these fields without eagerly
    importing the plugin's implementation modules.
    """

    name: str  # plugin key + config namespace (plugins.<name>)
    command_name: str  # CLI subcommand verb (e.g. "jobs")
    command_module: str  # dotted path, lazily imported on dispatch
    command_help: str  # top-level --help one-liner (rendered without import)
    # pydantic BaseModel subclass validating this plugin's config namespace,
    # attached to PluginsConfig. Plain type reference so _base.py imports no
    # plugin implementation (PluginsConfig is built in plugins.config).
    config_model: type[BaseModel] | None = None
