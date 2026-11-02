from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from daily_driver.tasks import Task


@dataclass(frozen=True)
class PackageDataDir:
    """One package-bundled directory of ``.md`` files rendered into the workspace.

    A plugin (or core) declares where its slash-command / agent markdown lives
    and where it should land under the workspace ``.claude/`` tree. ``generate``
    walks core's baseline dirs plus the ``package_data_dirs`` of each plugin
    switched on, so a plugin shipping slash-commands needs no change to core.
    """

    # dotted package holding the .md files (e.g. "daily_driver.resources.slash_commands")
    source_package: str
    # subdir under the workspace .claude/ (e.g. "commands/daily-driver")
    dest: str


class PluginConfigBase(BaseModel):
    """Base for the config model of a plugin that declares AI tasks.

    The plugin system, not each plugin, owns these two fields, so every plugin
    routes its tasks the same way. Provider names are checked against
    `ai.providers` by core when the whole config loads.
    """

    model_config = ConfigDict(extra="forbid")

    # Provider for every task this plugin declares.
    provider: str | None = None
    # Per-task override: task name -> provider name.
    tasks: dict[str, str] = {}


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
    # With a model the plugin is on only while its block exists and does not
    # set `enabled: false`; None means there is nothing to opt in with, so the
    # plugin is always on.
    config_model: type[BaseModel] | None = None
    # Dotted path to ``build_scheduled_jobs(ctx) -> list[ScheduledJob]``,
    # lazily imported by core.scheduler so core never eagerly loads plugin
    # implementation. None means the plugin contributes no launchd jobs.
    scheduled_jobs_builder: str | None = None
    # All launchd labels this plugin manages, swept unconditionally on
    # uninstall regardless of whether the job is currently configured.
    launchd_labels: tuple[str, ...] = ()
    # Dotted path to ``run_checks(workspace) -> list[CheckResult]``, lazily
    # imported by core.doctor. None means the plugin contributes no health
    # checks.
    doctor_checks: str | None = None
    # Package-bundled .md directories this plugin renders into the workspace
    # .claude/ tree on generate while it is on (slash-commands / agents). Empty
    # means the plugin ships none; core writes only its own baseline dirs.
    package_data_dirs: tuple[PackageDataDir, ...] = ()
    # AI tasks this plugin hands to a provider, routed under plugins.<name>.
    # A plugin with tasks needs a config_model subclassing PluginConfigBase.
    tasks: tuple[Task, ...] = ()
