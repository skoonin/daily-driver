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
    # Dotted path to ``build_scheduled_jobs(ctx) -> list[ScheduledJob]``,
    # lazily imported by core.scheduler so core never eagerly loads plugin
    # implementation. None means the plugin contributes no launchd jobs.
    scheduled_jobs_builder: str | None = None
    # All launchd labels this plugin manages, swept unconditionally on
    # uninstall regardless of whether the job is currently configured.
    launchd_labels: tuple[str, ...] = ()
    # launchd labels installed by previous releases of this plugin; core
    # scheduler sweeps them on install/uninstall so a renamed/relocated job
    # doesn't leave an orphaned plist firing the old argv.
    legacy_launchd_labels: tuple[str, ...] = ()
    # Dotted path to ``run_checks(workspace) -> list[CheckResult]``, lazily
    # imported by core.doctor. None means the plugin contributes no health
    # checks.
    doctor_checks: str | None = None
