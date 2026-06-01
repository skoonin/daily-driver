"""Job-search launchd job contributed to the core scheduler.

Core scheduler imports ``build_scheduled_jobs`` lazily (via the plugin's
``scheduled_jobs_builder`` dotted path), so this module is never loaded when
the scheduler runs for a workspace that does not configure the jobs scrape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daily_driver.core.scheduler import ScheduledJob, SchedulerContext

# launchd label for the daily jobs scrape. Owned by this plugin; core sweeps it
# on uninstall via Plugin.launchd_labels.
LABEL_SCRAPE_JOBS = "com.daily-driver.jobs"


def build_scheduled_jobs(ctx: SchedulerContext) -> list[ScheduledJob]:
    """Return the jobs-scrape launchd job when scheduler.jobs.time is set."""
    from daily_driver.core.scheduler import ScheduledJob

    scrape_cfg = ctx.merged_config.get("jobs", {})
    scrape_time_raw = scrape_cfg.get("time")
    if not scrape_time_raw:
        return []

    stdout, stderr = ctx.log_paths("jobs")
    scrape_args = [ctx.dd_bin, "jobs", "run", "--workspace", ctx.workspace_root]
    return [
        ScheduledJob(
            label=LABEL_SCRAPE_JOBS,
            template="jobs.plist.j2",
            program_arguments=scrape_args,
            template_package="daily_driver.plugins.job_search.templates",
            context={
                "label": LABEL_SCRAPE_JOBS,
                "program_arguments": scrape_args,
                "time": ctx.parse_hhmm(scrape_time_raw),
                "stdout_path": str(stdout),
                "stderr_path": str(stderr),
                "env_path": ctx.env_path,
                "home": ctx.home,
            },
        )
    ]
