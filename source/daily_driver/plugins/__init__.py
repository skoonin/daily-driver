import importlib.util

from daily_driver.plugins._base import Plugin
from daily_driver.plugins.job_search import PLUGIN as _job_search

PLUGINS: tuple[Plugin, ...] = (_job_search,)


def enabled_plugins(config: object) -> tuple[Plugin, ...]:
    """Registered plugins switched on in a workspace config.

    A plugin is on when its ``plugins.<name>`` block exists and does not set
    ``enabled: false``. A plugin with no config model has no block to opt in
    with, so it is always on. Every plugin effect (generated files, doctor
    checks, launchd jobs, CLI commands) is gated on this one answer. ``config``
    is typed loosely because plugins must not import core's Config.
    """
    plugins_cfg = getattr(config, "plugins", None)
    return tuple(plugin for plugin in PLUGINS if _is_on(plugin, plugins_cfg))


def off_reason(name: str) -> str:
    """The one phrasing for a plugin's command or job refusing to run."""
    return f"{name.replace('_', ' ')} is off in this workspace"


def switch_on_hint(name: str, config: object) -> str:
    """How to switch plugin `name` on, given the current config.

    A block that sets `enabled: false` gets "remove" rather than "add":
    pasting a second `plugins:` key would silently replace the whole block,
    settings included.
    """
    block = getattr(getattr(config, "plugins", None), name, None)
    if block is not None:
        return f"remove `enabled: false` from `plugins.{name}` in .dd-config.yaml"
    return f"add `plugins: {{{name}: {{}}}}` to .dd-config.yaml"


def _is_on(plugin: Plugin, plugins_cfg: object) -> bool:
    if plugin.config_model is None:
        return True
    block = getattr(plugins_cfg, plugin.name, None)
    return block is not None and getattr(block, "enabled", True)


def _validate_registry(plugins: tuple[Plugin, ...]) -> None:
    """Fail fast at import if a plugin descriptor is malformed.

    Catches the registration-time mistakes a plugin author makes — a typo'd
    hook path, a duplicate command, or an invalid config namespace — at process
    start, rather than when the affected command first runs. ``find_spec``
    resolves each hook's module without executing it, so this stays import-light
    (no plugin implementation is loaded here).
    """
    seen_commands: set[str] = set()
    for p in plugins:
        if not p.name.isidentifier():
            raise ValueError(
                f"plugin name {p.name!r} is not a valid identifier "
                "(it becomes a plugins.<name> config field)"
            )
        if p.command_name in seen_commands:
            raise ValueError(f"duplicate plugin command_name {p.command_name!r}")
        seen_commands.add(p.command_name)

        hook_paths = [p.command_module]
        if p.scheduled_jobs_builder:
            hook_paths.append(p.scheduled_jobs_builder.rpartition(".")[0])
        if p.doctor_checks:
            hook_paths.append(p.doctor_checks.rpartition(".")[0])
        hook_paths.extend(d.source_package for d in p.package_data_dirs)
        for module_path in hook_paths:
            if importlib.util.find_spec(module_path) is None:
                raise ValueError(
                    f"plugin {p.name!r} references unknown module {module_path!r}"
                )


_validate_registry(PLUGINS)
