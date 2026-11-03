from daily_driver.plugins._base import Plugin
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.tasks import Task

# Fit score, note, remote label and criteria answers for each job.
FIT_NOTES = Task("fit_notes", "text")

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
    launchd_labels=("com.daily-driver.jobs", "com.daily-driver.jobs-discover"),
    doctor_checks="daily_driver.plugins.job_search.doctor.run_checks",
    tasks=(FIT_NOTES,),
)
