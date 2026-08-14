"""Job-search launchd job contributed to the core scheduler.

Core scheduler imports ``build_scheduled_jobs`` lazily (via the plugin's
``scheduled_jobs_builder`` dotted path), so this module is never loaded when
the scheduler runs for a workspace that does not configure the jobs scrape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daily_driver.core.scheduler import ScheduledJob, SchedulerContext

# launchd labels owned by this plugin; core sweeps them on uninstall via
# Plugin.launchd_labels.
LABEL_SCRAPE_JOBS = "com.daily-driver.jobs"
LABEL_DISCOVER_BOARDS = "com.daily-driver.jobs-discover"


def _plist_job(
    ctx: SchedulerContext,
    *,
    label: str,
    log_name: str,
    action: list[str],
    cfg: dict[str, Any],
) -> list[ScheduledJob]:
    """One launchd job from a ``scheduler.<name>`` block; none when unset."""
    from daily_driver.core.scheduler import ScheduledJob

    time_raw = cfg.get("time")
    if not time_raw:
        return []

    stdout, stderr = ctx.log_paths(log_name)
    args = [ctx.dd_bin, *action, "--workspace", ctx.workspace_root]
    return [
        ScheduledJob(
            label=label,
            template="jobs.plist.j2",
            program_arguments=args,
            template_package="daily_driver.plugins.job_search.templates",
            context={
                "label": label,
                "program_arguments": args,
                "times": ctx.calendar_entries(
                    [ctx.parse_hhmm(time_raw)], ctx.parse_days(cfg.get("days"))
                ),
                "stdout_path": str(stdout),
                "stderr_path": str(stderr),
                "env_path": ctx.env_path,
                "home": ctx.home,
            },
        )
    ]


def build_scheduled_jobs(ctx: SchedulerContext) -> list[ScheduledJob]:
    """Return this plugin's launchd jobs for whichever times are configured.

    Discovery is its own job rather than a step inside the scrape: the board
    universe turns over far more slowly than postings do, launchd takes a
    single command (chaining would need a wrapper script), and a sweep that
    hangs must not block the scrape. A skipped sweep just means the scrape uses
    the previous board list, which is what happened before discovery could be
    scheduled at all.
    """
    return [
        *_plist_job(
            ctx,
            label=LABEL_SCRAPE_JOBS,
            log_name="jobs",
            action=["jobs", "run"],
            cfg=ctx.merged_config.get("jobs", {}),
        ),
        *_plist_job(
            ctx,
            label=LABEL_DISCOVER_BOARDS,
            log_name="jobs-discover",
            action=["jobs", "discover-boards"],
            cfg=ctx.merged_config.get("discovery", {}),
        ),
    ]
