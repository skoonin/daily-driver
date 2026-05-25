import importlib.util

from daily_driver.plugins._base import Plugin
from daily_driver.plugins.job_search import PLUGIN as _job_search

PLUGINS: tuple[Plugin, ...] = (_job_search,)


def _validate_registry(plugins: tuple[Plugin, ...]) -> None:
    """Fail fast at import if a plugin descriptor is malformed.

    Catches the registration-time mistakes a plugin author makes — a typo'd
    hook path, a duplicate command, an invalid config namespace, or a label
    listed as both current and legacy — at process start, rather than when the
    affected command first runs. ``find_spec`` resolves each hook's module
    without executing it, so this stays import-light (no plugin implementation
    is loaded here).
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

        overlap = set(p.launchd_labels) & set(p.legacy_launchd_labels)
        if overlap:
            raise ValueError(
                f"plugin {p.name!r} lists {sorted(overlap)} in both "
                "launchd_labels and legacy_launchd_labels"
            )

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
