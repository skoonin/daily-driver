from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from daily_driver.core.config_models import (
    Config,
    DailyDriverConfig,
    JobsConfig,
    JobSearchPlugin,
    Locations,
    PluginsConfig,
    RecurringTask,
    ScraperConfig,
    TrackerCategoryConfig,
    TrackerConfig,
    UserProfile,
)

# ---------------------------------------------------------------------------
# DailyDriverConfig
# ---------------------------------------------------------------------------


def test_daily_driver_config_defaults():
    m = DailyDriverConfig()
    assert m.output_dir == "."


def test_daily_driver_config_custom():
    m = DailyDriverConfig(output_dir="/home/user/notes")
    assert m.output_dir == "/home/user/notes"


def test_daily_driver_config_rejects_extra():
    with pytest.raises(ValidationError):
        DailyDriverConfig(output_dir=".", bogus_field=True)


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------


def test_user_profile_defaults():
    m = UserProfile()
    assert m.name is None
    assert m.citizenship == []
    assert m.work_auth == {}
    assert m.timezone is None
    assert m.seeking_since is None


def test_user_profile_full():
    m = UserProfile(
        name="Alice",
        citizenship=["US", "CA"],
        work_auth={"US": "citizen"},
        timezone="America/Vancouver",
        seeking_since="2026-01-01",
    )
    assert m.name == "Alice"
    assert m.seeking_since == date(2026, 1, 1)


def test_user_profile_seeking_since_native_date():
    # Unquoted YAML loads as a date object — validator must pass it through
    m = UserProfile(seeking_since=date(2026, 3, 15))
    assert m.seeking_since == date(2026, 3, 15)


# ---------------------------------------------------------------------------
# RecurringTask
# ---------------------------------------------------------------------------


def test_recurring_task_daily_no_day():
    m = RecurringTask(name="Standup", cadence="daily")
    assert m.cadence == "daily"
    assert m.day is None


def test_recurring_task_weekly_with_day():
    m = RecurringTask(name="Review", cadence="weekly", day="Monday")
    assert m.day == "Monday"


def test_recurring_task_day_rejected_for_daily():
    with pytest.raises(ValidationError, match="only valid when cadence is 'weekly'"):
        RecurringTask(name="X", cadence="daily", day="Monday")


def test_recurring_task_day_rejected_for_monthly():
    with pytest.raises(ValidationError):
        RecurringTask(name="X", cadence="monthly", day="Monday")


def test_recurring_task_invalid_cadence():
    with pytest.raises(ValidationError):
        RecurringTask(name="X", cadence="hourly")


# ---------------------------------------------------------------------------
# TrackerConfig
# ---------------------------------------------------------------------------


def test_tracker_config_valid():
    m = TrackerConfig(
        default_category="task",
        categories={"task": TrackerCategoryConfig(required=["title"])},
    )
    assert m.default_category == "task"


def test_tracker_config_default_category_missing_from_categories():
    with pytest.raises(ValidationError, match="must be a key in categories"):
        TrackerConfig(
            default_category="job",
            categories={"task": TrackerCategoryConfig()},
        )


def test_tracker_category_config_defaults():
    m = TrackerCategoryConfig()
    assert m.required == []


def test_tracker_config_warn_unknown_status_default_true():
    m = TrackerConfig(
        default_category="task",
        categories={"task": TrackerCategoryConfig(required=["title"])},
    )
    assert m.warn_unknown_status is True


def test_tracker_config_warn_unknown_status_can_be_disabled():
    m = TrackerConfig(
        default_category="task",
        categories={"task": TrackerCategoryConfig(required=["title"])},
        warn_unknown_status=False,
    )
    assert m.warn_unknown_status is False


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def test_locations_defaults():
    m = Locations()
    assert m.remote is False
    assert m.countries == []


# ---------------------------------------------------------------------------
# JobSearchPlugin
# ---------------------------------------------------------------------------


def test_job_search_plugin_minimal():
    m = JobSearchPlugin()
    assert m.persona is None
    assert m.sources == {}
    assert m.roles == []
    assert m.domain_keywords == []
    assert m.seniority_keywords == []
    assert m.min_comp_usd == 180000
    assert isinstance(m.scraper, ScraperConfig)


def test_job_search_plugin_with_sources():
    m = JobSearchPlugin(sources={"linkedin": {"max_pages": 3}})
    assert m.sources["linkedin"]["max_pages"] == 3


def test_job_search_plugin_full():
    m = JobSearchPlugin(
        persona="Staff SRE",
        roles=["Senior SRE", "Staff Platform Engineer"],
        domain_keywords=["kubernetes", "terraform"],
        seniority_keywords=["staff", "senior"],
        min_comp_usd=200000,
        scraper=ScraperConfig(enabled=True, timeout=60, parallel_workers=2),
    )
    assert m.persona == "Staff SRE"
    assert m.roles == ["Senior SRE", "Staff Platform Engineer"]
    assert m.min_comp_usd == 200000
    assert m.scraper.enabled is True
    assert m.scraper.timeout == 60
    assert m.scraper.parallel_workers == 2


def test_job_search_plugin_rejects_extra():
    with pytest.raises(ValidationError):
        JobSearchPlugin(nonexistent_key="oops")


def test_job_search_plugin_primary_currency_default_is_none():
    m = JobSearchPlugin()
    assert m.primary_currency is None


@pytest.mark.parametrize("code", ["USD", "CAD", "GBP", "EUR"])
def test_job_search_plugin_primary_currency_accepts_supported_codes(code):
    m = JobSearchPlugin(primary_currency=code)
    assert m.primary_currency == code


def test_job_search_plugin_primary_currency_rejects_unsupported():
    with pytest.raises(ValidationError):
        JobSearchPlugin(primary_currency="JPY")


# ---------------------------------------------------------------------------
# ScraperConfig
# ---------------------------------------------------------------------------


def test_scraper_config_defaults():
    m = ScraperConfig()
    assert m.enabled is False
    assert m.timeout == 30
    assert m.enrich_timeout == 30
    assert m.max_enrich_companies == 50
    assert m.enrich_gd_rating is True
    assert m.enrich_fit is True
    assert m.enrich_notes is True
    assert m.max_enrich_fit == 50
    assert m.detail_delay_seconds == 0.5
    assert m.search_terms is None
    assert m.headless is False
    assert m.wwr_categories == []
    assert m.hn_max_posts == 100
    assert m.greenhouse_boards == ["anthropic"]
    assert isinstance(m.jobs, JobsConfig)
    assert m.sources == {}
    assert m.parallel_workers == 4
    assert m.max_pages == 3


def test_scraper_config_rejects_extra():
    with pytest.raises(ValidationError):
        ScraperConfig(unknown_flag=True)


def test_scraper_config_sources_legacy_bool_coerced():
    """Legacy YAML form `sources: {remoteok: true}` migrates to SourceToggle."""
    from daily_driver.core.config_models import SourceToggle

    m = ScraperConfig(sources={"remoteok": True, "jobspy": False})
    assert isinstance(m.sources["remoteok"], SourceToggle)
    assert m.sources["remoteok"].enabled is True
    assert m.sources["jobspy"].enabled is False


def test_scraper_config_sources_typed_form():
    from daily_driver.core.config_models import SourceToggle

    m = ScraperConfig(sources={"linkedin": SourceToggle(enabled=True)})
    assert m.sources["linkedin"].enabled is True


def test_jobspy_toggle_per_site_flags():
    """jobspy entry coerces to JobspyToggle with per-site bool flags."""
    from daily_driver.core.config_models import JobspyToggle

    m = ScraperConfig(
        sources={
            "jobspy": {
                "enabled": True,
                "linkedin": False,
                "indeed": True,
                "google": True,
            }
        }
    )
    toggle = m.sources["jobspy"]
    assert isinstance(toggle, JobspyToggle)
    assert toggle.enabled is True
    assert toggle.linkedin is False
    assert toggle.indeed is True
    assert toggle.google is True


def test_jobspy_toggle_legacy_bool_coerced():
    from daily_driver.core.config_models import JobspyToggle

    m = ScraperConfig(sources={"jobspy": False})
    assert isinstance(m.sources["jobspy"], JobspyToggle)
    assert m.sources["jobspy"].enabled is False
    assert m.sources["jobspy"].linkedin is True


# ---------------------------------------------------------------------------
# JobsConfig
# ---------------------------------------------------------------------------


def test_jobs_config_defaults():
    m = JobsConfig()
    assert m.results_wanted_per_query == 50
    assert m.hours_old == 168
    assert m.country_indeed == "USA"


def test_jobs_config_custom():
    m = JobsConfig(results_wanted_per_query=100, hours_old=72, country_indeed="CA")
    assert m.results_wanted_per_query == 100
    assert m.country_indeed == "CA"


def test_jobs_config_rejects_extra():
    with pytest.raises(ValidationError):
        JobsConfig(bad_key="x")


def test_scraper_config_rejects_legacy_jobspy_key():
    """Stale ``scraper.jobspy:`` from pre-rename configs hard-fails via extra=forbid."""
    with pytest.raises(ValidationError):
        ScraperConfig(jobspy={"results_wanted_per_query": 100})


# ---------------------------------------------------------------------------
# PluginsConfig
# ---------------------------------------------------------------------------


def test_plugins_config_empty():
    m = PluginsConfig()
    assert m.job_search is None


def test_plugins_config_rejects_unknown_plugin():
    with pytest.raises(ValidationError):
        PluginsConfig(ticket_system={"url": "https://jira.example.com"})


# ---------------------------------------------------------------------------
# Top-level Config
# ---------------------------------------------------------------------------


def test_config_minimal_valid():
    m = Config(tracker=TrackerConfig(categories={"task": TrackerCategoryConfig()}))
    assert m.daily_driver.output_dir == "."
    assert m.user_profile.name is None
    assert m.plugins.job_search is None


def test_config_rejects_extra_top_level():
    """Root Config is extra='forbid' so typos fail loudly at parse time."""
    with pytest.raises(ValidationError):
        Config(
            tracker=TrackerConfig(categories={"task": TrackerCategoryConfig()}),
            unknown_section={"foo": "bar"},
        )


def test_config_rejects_extra_in_plugins():
    with pytest.raises(ValidationError):
        Config(
            tracker=TrackerConfig(categories={"task": TrackerCategoryConfig()}),
            plugins={"job_search": None, "mystery_plugin": {}},
        )


# ---------------------------------------------------------------------------
# F3 ClaudeConfig + F4 ScheduleConfig
# ---------------------------------------------------------------------------


def test_claude_config_default_resume_off():
    from daily_driver.core.config_models import ClaudeConfig

    m = ClaudeConfig()
    assert m.resume_check_in is False


def test_claude_config_rejects_extra():
    from daily_driver.core.config_models import ClaudeConfig

    with pytest.raises(ValidationError):
        ClaudeConfig(resume_check_in=True, bogus=1)


def test_schedule_config_default_both_none():
    from daily_driver.core.config_models import ScheduleConfig

    m = ScheduleConfig()
    assert m.day_start is None
    assert m.day_end is None


def test_schedule_config_accepts_hhmm_string():
    from daily_driver.core.config_models import ScheduleConfig

    m = ScheduleConfig(day_start="07:00", day_end="17:30")
    assert m.day_start == "07:00"
    assert m.day_end == "17:30"


def test_schedule_config_coerces_yaml_int():
    """PyYAML parses unquoted HH:MM as base-60 int; we coerce back to string."""
    from daily_driver.core.config_models import ScheduleConfig

    # 17:30 -> 17*60 + 30 = 1050 ; 07:00 -> 420
    m = ScheduleConfig(day_start=420, day_end=1050)
    assert m.day_start == "07:00"
    assert m.day_end == "17:30"


def test_schedule_config_rejects_invalid_hhmm():
    from daily_driver.core.config_models import ScheduleConfig

    for bad in ("9:99", "25:00", "noon", 99999):
        with pytest.raises(ValidationError):
            ScheduleConfig(day_start=bad)


# ---------------------------------------------------------------------------
# SchedulerConfig
# ---------------------------------------------------------------------------


def test_scheduler_config_default_both_none():
    from daily_driver.core.config_models import SchedulerConfig

    m = SchedulerConfig()
    assert m.checkin is None
    assert m.jobs is None


def test_scheduler_config_typed_round_trip():
    from daily_driver.core.config_models import SchedulerConfig

    m = SchedulerConfig.model_validate(
        {"checkin": {"times": ["09:00", "13:00"]}, "jobs": {"time": "06:30"}}
    )
    assert m.checkin is not None and m.checkin.times == ["09:00", "13:00"]
    assert m.jobs is not None and m.jobs.time == "06:30"


def test_scheduler_config_rejects_extra_top_level_key():
    from daily_driver.core.config_models import SchedulerConfig

    with pytest.raises(ValidationError):
        SchedulerConfig.model_validate({"bogus": {"time": "07:00"}})


def test_scheduler_config_rejects_legacy_scrape_jobs_key():
    """Pre-rename `scheduler.scrape_jobs:` now fails at parse time via extra=forbid."""
    from daily_driver.core.config_models import SchedulerConfig

    with pytest.raises(ValidationError, match="scrape_jobs"):
        SchedulerConfig.model_validate({"scrape_jobs": {"time": "07:00"}})


def test_checkin_schedule_rejects_extra_key():
    from daily_driver.core.config_models import CheckinSchedule

    with pytest.raises(ValidationError):
        CheckinSchedule.model_validate({"times": ["09:00"], "bogus": 1})


def test_job_schedule_rejects_extra_key():
    from daily_driver.core.config_models import JobSchedule

    with pytest.raises(ValidationError):
        JobSchedule.model_validate({"time": "07:00", "bogus": 1})


# ---------------------------------------------------------------------------
# AIConfig
# ---------------------------------------------------------------------------


def test_ai_config_defaults_to_claude_everywhere():
    from daily_driver.core.config_models import AIConfig

    ai = AIConfig()
    assert ai.enrichment.provider == "claude"
    assert ai.enrichment.model is None
    assert ai.summary.provider == "claude"
    assert ai.summary.model is None
    assert ai.ollama.endpoint == "http://localhost:11434"
    assert ai.ollama.timeout == 60


def test_ai_config_per_task_ollama_with_model():
    from daily_driver.core.config_models import AIConfig

    ai = AIConfig.model_validate(
        {
            "enrichment": {"provider": "ollama", "model": "qwen2.5:14b"},
            "summary": {"provider": "claude", "model": "sonnet"},
            "ollama": {"endpoint": "http://10.0.0.5:11434", "timeout": 120},
        }
    )
    assert ai.enrichment.provider == "ollama"
    assert ai.enrichment.model == "qwen2.5:14b"
    assert ai.summary.provider == "claude"
    assert ai.summary.model == "sonnet"
    assert ai.ollama.endpoint == "http://10.0.0.5:11434"
    assert ai.ollama.timeout == 120


def test_ai_task_rejects_unknown_provider():
    from daily_driver.core.config_models import AITaskConfig

    with pytest.raises(ValidationError):
        AITaskConfig(provider="vertex")  # type: ignore[arg-type]


def test_ai_config_rejects_extra_keys():
    from daily_driver.core.config_models import AIConfig

    with pytest.raises(ValidationError):
        AIConfig.model_validate({"unknown": {}})


def test_ai_task_rejects_extra_keys():
    from daily_driver.core.config_models import AITaskConfig

    with pytest.raises(ValidationError):
        AITaskConfig.model_validate({"provider": "claude", "temperature": 0.1})


def test_ollama_config_rejects_extra_keys():
    from daily_driver.core.config_models import OllamaConfig

    with pytest.raises(ValidationError):
        OllamaConfig.model_validate({"endpoint": "x", "format": "json"})


def test_root_config_omitting_ai_block_uses_defaults():
    """Backwards-compat: omitting `ai:` keeps claude-only behavior."""
    c = Config(tracker=TrackerConfig(categories={"task": TrackerCategoryConfig()}))
    assert c.ai.enrichment.provider == "claude"
    assert c.ai.summary.provider == "claude"


def test_every_field_has_description():
    """Each model field must carry a non-None description for codegen."""
    from pydantic import BaseModel as _BM

    import daily_driver.core.config_models as cm

    failures: list[str] = []
    for name in dir(cm):
        obj = getattr(cm, name)
        if not isinstance(obj, type) or not issubclass(obj, _BM) or obj is _BM:
            continue
        for fname, finfo in obj.model_fields.items():
            extra = dict(finfo.json_schema_extra or {})
            if extra.get("template_skip"):
                continue
            if finfo.description is None:
                failures.append(f"{obj.__name__}.{fname}")
    assert not failures, f"missing description: {failures}"
