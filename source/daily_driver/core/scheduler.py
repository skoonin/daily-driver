"""Scheduler: render and install launchd plists for daily-driver jobs.

macOS-only for v0.1.0 — callers should gate on sys.platform before entering
install/uninstall paths. Pure rendering (build_jobs, render_plist) is
platform-neutral and unit-testable without launchctl.
"""

from __future__ import annotations

import importlib
import importlib.resources
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from daily_driver.core.workspace import Workspace
from daily_driver.integrations import launchd as launchd_int


class SchedulerError(Exception):
    pass


_LABEL_CHECKIN = "com.daily-driver.checkin"
_LABEL_DAY_START = "com.daily-driver.day-start"
_LABEL_DAY_END = "com.daily-driver.day-end"

# Core (non-plugin) labels swept on uninstall. Plugin-contributed labels are
# swept via PLUGINS (Plugin.launchd_labels) in uninstall_all.
_CORE_LABELS = (_LABEL_CHECKIN, _LABEL_DAY_START, _LABEL_DAY_END)

_TIME_RE = re.compile(r"^([0-1]?\d|2[0-3]):([0-5]\d)$")

# launchd StartCalendarInterval Weekday integers: 0 = Sunday .. 6 = Saturday.
_WEEKDAY_NUMS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def parse_hhmm(raw: str) -> dict[str, int]:
    m = _TIME_RE.match(raw.strip())
    if m is None:
        raise SchedulerError(f"invalid HH:MM time string: {raw!r}")
    return {"hour": int(m.group(1)), "minute": int(m.group(2))}


def parse_days(raw: Any) -> list[int] | None:
    """Resolve a `days` cadence value to launchd Weekday integers.

    None means every day (no Weekday key in the calendar interval). Accepts
    the shortcuts "daily" / "weekdays" or a list of day names — validated
    defensively because packaged scheduler defaults arrive as raw YAML, not
    through the pydantic config models.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        shortcut = raw.strip().lower()
        if shortcut == "daily":
            return None
        if shortcut == "weekdays":
            return [1, 2, 3, 4, 5]
        raise SchedulerError(
            f"invalid days value: {raw!r} (expected 'daily', 'weekdays',"
            " or a list of day names)"
        )
    if isinstance(raw, list):
        weekdays: list[int] = []
        for item in raw:
            name = str(item).strip().lower()[:3]
            if name not in _WEEKDAY_NUMS:
                raise SchedulerError(f"invalid day name: {item!r}")
            if _WEEKDAY_NUMS[name] not in weekdays:
                weekdays.append(_WEEKDAY_NUMS[name])
        if not weekdays:
            return None
        return weekdays
    raise SchedulerError(f"invalid days value: {raw!r}")


def calendar_entries(
    times: list[dict[str, int]], weekdays: list[int] | None
) -> list[dict[str, int]]:
    """Expand times x weekdays into StartCalendarInterval entries.

    launchd takes one {Weekday, Hour, Minute} dict per day-and-time
    combination; None weekdays means daily (entries carry no weekday key).
    """
    if weekdays is None:
        return times
    return [{**t, "weekday": d} for d in weekdays for t in times]


@dataclass(frozen=True)
class ScheduledJob:
    label: str
    template: str
    program_arguments: list[str]
    context: dict[str, Any]
    # Package holding the jinja template. Plugin jobs ship their own template
    # alongside the plugin; core jobs use daily_driver.resources.launchd.
    template_package: str = "daily_driver.resources.launchd"

    @property
    def stdout_path(self) -> str:
        return str(self.context["stdout_path"])

    @property
    def stderr_path(self) -> str:
        return str(self.context["stderr_path"])


@dataclass(frozen=True)
class SchedulerContext:
    """Shared primitives passed to plugin scheduled-job builders.

    Bundles the resolved executable, environment, and merged scheduler config
    so a plugin builder constructs ScheduledJobs without re-deriving them or
    importing core scheduler internals.
    """

    merged_config: dict[str, Any]
    dd_bin: str
    home: str
    env_path: str
    workspace_root: str
    log_paths: Callable[[str], tuple[Path, Path]]
    parse_hhmm: Callable[[str], dict[str, int]] = field(default=parse_hhmm)
    parse_days: Callable[[Any], list[int] | None] = field(default=parse_days)
    calendar_entries: Callable[
        [list[dict[str, int]], list[int] | None], list[dict[str, int]]
    ] = field(default=calendar_entries)


def _default_scheduler_config() -> dict[str, Any]:
    raw = (
        importlib.resources.files("daily_driver.resources.templates")
        .joinpath("scheduler.default.yaml")
        .read_text(encoding="utf-8")
    )
    return yaml.safe_load(raw) or {}


def _merge_scheduler_config(workspace: Workspace) -> dict[str, Any]:
    defaults = _default_scheduler_config()
    # exclude_none preserves shallow-merge-over-defaults: a user setting only
    # `checkin` keeps the packaged-default `jobs` (and vice versa).
    user_cfg = (
        workspace.config.scheduler.model_dump(exclude_none=True)
        if workspace.config.scheduler is not None
        else {}
    )
    merged: dict[str, Any] = {**defaults}
    for key, value in user_cfg.items():
        merged[key] = value
    return merged


def _log_paths(workspace: Workspace, job_name: str) -> tuple[Path, Path]:
    logs_dir = workspace.ephemeral_dir / "logs"
    return (
        logs_dir / f"launchd-{job_name}.out",
        logs_dir / f"launchd-{job_name}.err",
    )


def _env_path() -> str:
    return os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")


def _daily_driver_cmd() -> str:
    """Resolve the daily-driver executable; falls back to 'daily-driver' on PATH."""
    return shutil.which("daily-driver") or "daily-driver"


def build_jobs(workspace: Workspace) -> list[ScheduledJob]:
    """Read workspace + defaults; return jobs to install."""
    cfg = _merge_scheduler_config(workspace)
    jobs: list[ScheduledJob] = []
    dd_bin = _daily_driver_cmd()
    home = str(Path.home())
    env_path = _env_path()
    workspace_root = str(workspace.root)

    checkin_cfg = cfg.get("checkin", {})
    checkin_times = [parse_hhmm(t) for t in checkin_cfg.get("times", [])]
    checkin_days = parse_days(checkin_cfg.get("days"))
    if checkin_times:
        stdout, stderr = _log_paths(workspace, "checkin")
        checkin_args = [dd_bin, "check-in", "--workspace", workspace_root]
        jobs.append(
            ScheduledJob(
                label=_LABEL_CHECKIN,
                template="checkin.plist.j2",
                program_arguments=checkin_args,
                context={
                    "label": _LABEL_CHECKIN,
                    "program_arguments": checkin_args,
                    "times": calendar_entries(checkin_times, checkin_days),
                    "stdout_path": str(stdout),
                    "stderr_path": str(stderr),
                    "env_path": env_path,
                    "home": home,
                },
            )
        )

    schedule_cfg = workspace.config.schedule
    # One cadence for both day-start and day-end: `schedule.days`.
    schedule_days = parse_days(schedule_cfg.days)
    if schedule_cfg.day_start:
        stdout, stderr = _log_paths(workspace, "day-start")
        ds_args = [dd_bin, "day-start", "--workspace", workspace_root]
        jobs.append(
            ScheduledJob(
                label=_LABEL_DAY_START,
                template="checkin.plist.j2",
                program_arguments=ds_args,
                context={
                    "label": _LABEL_DAY_START,
                    "program_arguments": ds_args,
                    "times": calendar_entries(
                        [parse_hhmm(schedule_cfg.day_start)], schedule_days
                    ),
                    "stdout_path": str(stdout),
                    "stderr_path": str(stderr),
                    "env_path": env_path,
                    "home": home,
                },
            )
        )

    if schedule_cfg.day_end:
        stdout, stderr = _log_paths(workspace, "day-end")
        de_args = [dd_bin, "day-end", "--workspace", workspace_root]
        jobs.append(
            ScheduledJob(
                label=_LABEL_DAY_END,
                template="checkin.plist.j2",
                program_arguments=de_args,
                context={
                    "label": _LABEL_DAY_END,
                    "program_arguments": de_args,
                    "times": calendar_entries(
                        [parse_hhmm(schedule_cfg.day_end)], schedule_days
                    ),
                    "stdout_path": str(stdout),
                    "stderr_path": str(stderr),
                    "env_path": env_path,
                    "home": home,
                },
            )
        )

    ctx = SchedulerContext(
        merged_config=cfg,
        dd_bin=dd_bin,
        home=home,
        env_path=env_path,
        workspace_root=workspace_root,
        log_paths=lambda name: _log_paths(workspace, name),
    )
    jobs.extend(_plugin_jobs(ctx))

    return jobs


def _plugin_jobs(ctx: SchedulerContext) -> list[ScheduledJob]:
    """Collect scheduled jobs each plugin contributes.

    Plugin builders are resolved by dotted path and imported lazily so core
    never eagerly loads plugin implementation modules at import time.
    """
    from daily_driver.plugins import PLUGINS

    collected: list[ScheduledJob] = []
    for plugin in PLUGINS:
        if plugin.scheduled_jobs_builder is None:
            continue
        module_path, _, attr = plugin.scheduled_jobs_builder.rpartition(".")
        builder = getattr(importlib.import_module(module_path), attr)
        collected.extend(builder(ctx))
    return collected


def _plugin_managed_labels() -> tuple[str, ...]:
    """All launchd labels owned by plugins, swept unconditionally on uninstall."""
    from daily_driver.plugins import PLUGINS

    return tuple(label for plugin in PLUGINS for label in plugin.launchd_labels)


def render_plist(job: ScheduledJob) -> str:
    with importlib.resources.as_file(
        importlib.resources.files(job.template_package)
    ) as templates_dir:
        env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        tmpl = env.get_template(job.template)
        return tmpl.render(**job.context)


def _state_launchd_dir(workspace: Workspace) -> Path:
    return workspace.ephemeral_dir / "launchd"


def install_all(workspace: Workspace) -> list[str]:
    """Render, write, and load plists for all configured jobs.

    Idempotent: unloads any existing plist first, then reinstalls — picks up
    environment / command-path changes on re-run.

    Returns the list of labels installed.
    """
    try:
        launchd_int.require_macos()
    except launchd_int.LaunchdUnavailableError as exc:
        raise SchedulerError(str(exc)) from exc

    jobs = build_jobs(workspace)
    installed: list[str] = []

    for job in jobs:
        content = render_plist(job)

        # Log directory must exist before launchd tries to open StandardOutPath.
        Path(job.stdout_path).parent.mkdir(parents=True, exist_ok=True)

        # Mirror the plist into workspace state for inspection / debugging.
        state_dir = _state_launchd_dir(workspace)
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / f"{job.label}.plist").write_text(content, encoding="utf-8")

        launchd_int.unload(job.label)
        launchd_int.write_plist(job.label, content)
        try:
            launchd_int.load(job.label)
        except launchd_int.LaunchdLoadError as exc:
            raise SchedulerError(str(exc)) from exc
        installed.append(job.label)

    return installed


def uninstall_all(workspace: Workspace) -> list[str]:
    """Unload + remove plists for all known job labels.

    Always also removes the mirrored copy under `.daily-driver/state/launchd/`.
    Returns the list of labels that had plists present and were removed.
    """
    try:
        launchd_int.require_macos()
    except launchd_int.LaunchdUnavailableError as exc:
        raise SchedulerError(str(exc)) from exc

    removed: list[str] = []
    for label in (
        *_CORE_LABELS,
        *_plugin_managed_labels(),
    ):
        launchd_int.unload(label)
        if launchd_int.remove(label):
            removed.append(label)

    state_dir = _state_launchd_dir(workspace)
    if state_dir.exists():
        # rmtree handles non-empty trees and is idempotent under ignore_errors —
        # safer than iterdir + unlink which crashes if a non-plist subdir lands here.
        shutil.rmtree(state_dir, ignore_errors=True)

    return removed


__all__ = [
    "ScheduledJob",
    "SchedulerError",
    "build_jobs",
    "calendar_entries",
    "install_all",
    "parse_days",
    "parse_hhmm",
    "render_plist",
    "uninstall_all",
]
