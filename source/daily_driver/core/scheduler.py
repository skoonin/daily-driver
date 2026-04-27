"""Scheduler: render and install launchd plists for daily-driver jobs.

macOS-only for v0.1.0 — callers should gate on sys.platform before entering
install/uninstall paths. Pure rendering (build_jobs, render_plist) is
platform-neutral and unit-testable without launchctl.
"""

from __future__ import annotations

import importlib.resources
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from daily_driver.core.workspace import Workspace
from daily_driver.integrations import launchd as launchd_int


class SchedulerError(Exception):
    pass


_LABEL_CHECKIN = "com.daily-driver.checkin"
_LABEL_SCRAPE_JOBS = "com.daily-driver.scrape-jobs"

_TIME_RE = re.compile(r"^([0-1]?\d|2[0-3]):([0-5]\d)$")


def _parse_hhmm(raw: str) -> dict[str, int]:
    m = _TIME_RE.match(raw.strip())
    if m is None:
        raise SchedulerError(f"invalid HH:MM time string: {raw!r}")
    return {"hour": int(m.group(1)), "minute": int(m.group(2))}


@dataclass(frozen=True)
class ScheduledJob:
    label: str
    template: str
    program_arguments: list[str]
    context: dict[str, Any]

    @property
    def stdout_path(self) -> str:
        return str(self.context["stdout_path"])

    @property
    def stderr_path(self) -> str:
        return str(self.context["stderr_path"])


def _default_scheduler_config() -> dict[str, Any]:
    raw = (
        importlib.resources.files("daily_driver.templates")
        .joinpath("scheduler.default.yaml")
        .read_text(encoding="utf-8")
    )
    return yaml.safe_load(raw) or {}


def _merge_scheduler_config(workspace: Workspace) -> dict[str, Any]:
    defaults = _default_scheduler_config()
    user_cfg = workspace.config.scheduler or {}
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
    checkin_times = [_parse_hhmm(t) for t in checkin_cfg.get("times", [])]
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
                    "times": checkin_times,
                    "stdout_path": str(stdout),
                    "stderr_path": str(stderr),
                    "env_path": env_path,
                    "home": home,
                },
            )
        )

    scrape_cfg = cfg.get("scrape_jobs", {})
    scrape_time_raw = scrape_cfg.get("time")
    if scrape_time_raw:
        stdout, stderr = _log_paths(workspace, "scrape-jobs")
        scrape_args = [dd_bin, "scrape-jobs", "run", "--workspace", workspace_root]
        jobs.append(
            ScheduledJob(
                label=_LABEL_SCRAPE_JOBS,
                template="scrape-jobs.plist.j2",
                program_arguments=scrape_args,
                context={
                    "label": _LABEL_SCRAPE_JOBS,
                    "program_arguments": scrape_args,
                    "time": _parse_hhmm(scrape_time_raw),
                    "stdout_path": str(stdout),
                    "stderr_path": str(stderr),
                    "env_path": env_path,
                    "home": home,
                },
            )
        )

    return jobs


def render_plist(job: ScheduledJob) -> str:
    with importlib.resources.as_file(
        importlib.resources.files("daily_driver.launchd")
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

        # Mirror the plist into workspace state so uninstall --keep-state can preserve it.
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


def uninstall_all(workspace: Workspace, keep_state: bool = False) -> list[str]:
    """Unload + remove plists for all known job labels.

    --keep-state retains the mirrored copy under `.daily-driver/state/launchd/`;
    otherwise the mirror is also removed.

    Returns the list of labels that had plists present and were removed.
    """
    try:
        launchd_int.require_macos()
    except launchd_int.LaunchdUnavailableError as exc:
        raise SchedulerError(str(exc)) from exc

    removed: list[str] = []
    for label in (_LABEL_CHECKIN, _LABEL_SCRAPE_JOBS):
        launchd_int.unload(label)
        if launchd_int.remove(label):
            removed.append(label)

    state_dir = _state_launchd_dir(workspace)
    if state_dir.exists() and not keep_state:
        for child in state_dir.iterdir():
            child.unlink()
        state_dir.rmdir()

    return removed


__all__ = [
    "ScheduledJob",
    "SchedulerError",
    "build_jobs",
    "install_all",
    "render_plist",
    "uninstall_all",
]
