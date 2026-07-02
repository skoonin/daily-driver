from daily_driver.plugins._base import Plugin
from daily_driver.plugins.job_search.config import JobSearchPlugin

PLUGIN = Plugin(
    name="job_search",
    command_name="jobs",
    command_module="daily_driver.plugins.job_search.cli",
    command_help=(
        "Job search: run scrapes, backfill enrichment, promote matches, "
        "inspect status, prune stale rows"
    ),
    config_model=JobSearchPlugin,
    scheduled_jobs_builder=(
        "daily_driver.plugins.job_search.scheduler.build_scheduled_jobs"
    ),
    launchd_labels=("com.daily-driver.jobs",),
    doctor_checks="daily_driver.plugins.job_search.doctor.run_checks",
)
