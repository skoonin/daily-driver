from daily_driver.plugins._base import Plugin
from daily_driver.plugins.job_search import PLUGIN as _job_search

PLUGINS: tuple[Plugin, ...] = (_job_search,)
