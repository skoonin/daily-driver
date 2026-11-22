from daily_driver.plugins._base import HelpTopic, Plugin
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.tasks import Task

# Fit score, note, remote label and criteria answers for each job.
FIT_NOTES = Task("fit_notes", "text")

# One vocabulary for jobs.csv rows and `job`-category tracker entries, so a
# promoted row keeps its status. Both share the status machinery in core.statuses.
JOBS_RECOMMENDED_STATUSES: tuple[str, ...] = (
    "found",
    "pending",
    "skipped",
    "applied",
    "interviewing",
    "rejected",
    "dropped",
    # `closed` is a shipped jobs_archive.DEFAULT_PRUNE_STATUSES target, so it
    # must be recognized or every prune would warn about the tool's own default.
    "closed",
)

_ROUTE_THROUGH_PROVIDERS = (
    "delete it and the indented lines under it; set "
    "plugins.job_search.provider, or plugins.job_search.tasks.fit_notes, "
    "to a provider under ai.providers."
)

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
    init_flag_help=(
        "Switch on job search: `daily-driver jobs`, its settings, and the "
        "job sections of the Claude prompts."
    ),
    tracker_statuses={"job": JOBS_RECOMMENDED_STATUSES},
    help_topics=(
        HelpTopic(
            name="sources",
            header="[bold]Sources[/bold] (consumer: jobs run --sources):",
            values="daily_driver.plugins.job_search.scraper.sources.source_names",
            consumers=("jobs run --sources", "sources.<name>.enabled"),
        ),
    ),
    date_consumers=("jobs prune --older-than",),
    removed_settings={
        "plugins.job_search.persona": (
            "delete it and the indented lines under it; job reviews read your "
            "roles and context.md instead."
        ),
        "plugins.job_search.enrichment.provider": _ROUTE_THROUGH_PROVIDERS,
        "plugins.job_search.enrichment.model": _ROUTE_THROUGH_PROVIDERS,
        "plugins.job_search.enrichment.fit_notes": _ROUTE_THROUGH_PROVIDERS,
        "scheduler.jobs": (
            "delete it and the indented lines under it; set "
            "plugins.job_search.scheduler.run instead, where `enabled: false` "
            "turns the run off."
        ),
        "scheduler.discovery": (
            "delete it and the indented lines under it; set "
            "plugins.job_search.scheduler.discovery instead, where `enabled: false` "
            "turns the run off."
        ),
    },
)
