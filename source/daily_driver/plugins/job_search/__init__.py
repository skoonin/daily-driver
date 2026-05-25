from daily_driver.plugins._base import Plugin

PLUGIN = Plugin(
    name="job_search",
    command_name="jobs",
    command_module="daily_driver.cli.commands.jobs",
    command_help="Job search: scrape boards, inspect status, prune stale rows",
)
