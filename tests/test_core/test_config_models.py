from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from daily_driver.core.config_models import (
    Config,
    DailyDriverConfig,
    InteractiveAIConfig,
    PluginsConfig,
    RecurringTask,
    TrackerCategoryConfig,
    TrackerConfig,
    UserProfile,
)
from daily_driver.plugins.job_search.config import (
    EnrichmentConfig,
    JobSearchPlugin,
    Locations,
    ScraperConfig,
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
# InteractiveAIConfig (model-only; provider must be rejected, not ignored)
# ---------------------------------------------------------------------------


def test_interactive_ai_config_defaults_model_none():
    assert InteractiveAIConfig().model is None


def test_interactive_ai_config_accepts_model():
    assert InteractiveAIConfig(model="sonnet").model == "sonnet"


def test_interactive_ai_config_rejects_provider():
    # The launchers are claude-only, so a provider knob would be inert; the
    # model rejects it loudly rather than accepting-and-ignoring it.
    with pytest.raises(ValidationError):
        InteractiveAIConfig(provider="ollama")


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
    assert m.countries == {}


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
    assert isinstance(m.scraper, ScraperConfig)
    assert isinstance(m.enrichment, EnrichmentConfig)
    assert isinstance(m.sources, dict)


def test_job_search_plugin_with_sources():
    m = JobSearchPlugin(sources={"remoteok": True})
    assert m.sources["remoteok"].enabled is True


def test_job_search_plugin_full():
    m = JobSearchPlugin(
        persona="Staff SRE",
        roles=["Senior SRE", "Staff Platform Engineer"],
        domain_keywords=["kubernetes", "terraform"],
        seniority_keywords=["staff", "senior"],
        scraper=ScraperConfig(enabled=True, timeout=60, parallel_workers=2),
    )
    assert m.persona == "Staff SRE"
    assert m.roles == ["Senior SRE", "Staff Platform Engineer"]
    assert m.scraper.enabled is True
    assert m.scraper.timeout == 60
    assert m.scraper.parallel_workers == 2


def test_job_search_plugin_rejects_extra():
    with pytest.raises(ValidationError):
        JobSearchPlugin(nonexistent_key="oops")


# ---------------------------------------------------------------------------
# ScraperConfig
# ---------------------------------------------------------------------------


def test_scraper_config_defaults():
    m = ScraperConfig()
    assert m.enabled is False
    assert m.max_retries == 3
    assert m.max_age_days == 30
    assert m.timeout == 30
    assert m.search_terms is None
    assert m.headless is False
    assert m.parallel_workers == 8
    assert m.max_pages == 3
    assert m.browser == "firefox"


def test_scraper_config_rejects_extra():
    with pytest.raises(ValidationError):
        ScraperConfig(unknown_flag=True)


def test_scraper_config_accepts_known_browsers():
    for engine in ("firefox", "chromium", "webkit"):
        assert ScraperConfig(browser=engine).browser == engine


def test_scraper_config_rejects_unknown_browser():
    with pytest.raises(ValidationError):
        ScraperConfig(browser="safari")


def test_scraper_config_rejects_moved_enrichment_field():
    """Enrichment knobs moved to EnrichmentConfig; the old flat field hard-fails."""
    with pytest.raises(ValidationError):
        ScraperConfig(enrich_timeout=5)


def test_scraper_config_rejects_moved_sources_field():
    """`sources` is a sibling of `scraper`; a flat `sources:` under scraper hard-fails."""
    with pytest.raises(ValidationError):
        ScraperConfig(sources={"remoteok": True})


# ---------------------------------------------------------------------------
# EnrichmentConfig
# ---------------------------------------------------------------------------


def test_enrichment_config_defaults():
    m = EnrichmentConfig()
    assert m.enrich_timeout == 30
    assert m.enrich_fit is True
    assert m.enrich_notes is True
    assert m.max_enrich_fit == 50
    assert m.detail_delay_seconds == 0.5


def test_enrichment_config_rejects_extra():
    with pytest.raises(ValidationError):
        EnrichmentConfig(unknown_flag=True)


# ---------------------------------------------------------------------------
# JobSearchPlugin.sources
# ---------------------------------------------------------------------------


def test_sources_defaults():
    m = JobSearchPlugin()
    assert m.sources == {}


def test_sources_legacy_bool_coerced():
    """YAML form `sources: {remoteok: true}` migrates to SourceToggle."""
    from daily_driver.plugins.job_search.config import SourceToggle

    m = JobSearchPlugin(sources={"remoteok": True, "linkedin": False})
    assert isinstance(m.sources["remoteok"], SourceToggle)
    assert m.sources["remoteok"].enabled is True
    assert m.sources["linkedin"].enabled is False


def test_sources_typed_form():
    from daily_driver.plugins.job_search.config import SourceToggle

    m = JobSearchPlugin(sources={"linkedin": SourceToggle(enabled=True)})
    assert m.sources["linkedin"].enabled is True


def test_sources_per_source_knobs_on_toggles():
    """Per-source knobs live on their SourceToggle subclass, not flat on scraper."""
    from daily_driver.plugins.job_search.config import (
        GreenhouseToggle,
        HackerNewsToggle,
        WeWorkRemotelyToggle,
    )

    m = JobSearchPlugin(
        sources={
            "weworkremotely": {"enabled": True, "wwr_categories": ["devops"]},
            "greenhouse": {"enabled": True, "greenhouse_boards": ["stripe"]},
            "hn_jobs": {"enabled": True, "hn_max_posts": 25},
        }
    )
    assert isinstance(m.sources["weworkremotely"], WeWorkRemotelyToggle)
    assert m.sources["weworkremotely"].wwr_categories == ["devops"]
    assert isinstance(m.sources["greenhouse"], GreenhouseToggle)
    assert m.sources["greenhouse"].greenhouse_boards == ["stripe"]
    assert isinstance(m.sources["hn_jobs"], HackerNewsToggle)
    assert m.sources["hn_jobs"].hn_max_posts == 25


def test_linkedin_toggle_defaults():
    """`linkedin` is a top-level site source carrying its own query knobs."""
    from daily_driver.plugins.job_search.config import LinkedInToggle

    m = JobSearchPlugin(sources={"linkedin": {"enabled": True}})
    toggle = m.sources["linkedin"]
    assert isinstance(toggle, LinkedInToggle)
    assert toggle.enabled is True
    assert toggle.results_wanted_per_query == 50
    assert toggle.hours_old == 168
    # LinkedIn takes no country param (scrape_jobs has no linkedin country knob).
    assert not hasattr(toggle, "country")


def test_indeed_toggle_defaults():
    """`indeed` is a top-level site source; `country` lives here, not on linkedin."""
    from daily_driver.plugins.job_search.config import IndeedToggle

    m = JobSearchPlugin(sources={"indeed": {"enabled": True}})
    toggle = m.sources["indeed"]
    assert isinstance(toggle, IndeedToggle)
    assert toggle.enabled is True
    assert toggle.results_wanted_per_query == 50
    assert toggle.hours_old == 168
    assert toggle.country == "USA"


def test_indeed_toggle_custom_knobs():
    from daily_driver.plugins.job_search.config import IndeedToggle

    m = JobSearchPlugin(
        sources={
            "indeed": {
                "enabled": True,
                "results_wanted_per_query": 100,
                "hours_old": 72,
                "country": "CA",
            }
        }
    )
    toggle = m.sources["indeed"]
    assert isinstance(toggle, IndeedToggle)
    assert toggle.results_wanted_per_query == 100
    assert toggle.hours_old == 72
    assert toggle.country == "CA"


def test_site_toggle_legacy_bool_coerced():
    from daily_driver.plugins.job_search.config import IndeedToggle, LinkedInToggle

    m = JobSearchPlugin(sources={"linkedin": True, "indeed": False})
    assert isinstance(m.sources["linkedin"], LinkedInToggle)
    assert m.sources["linkedin"].enabled is True
    assert isinstance(m.sources["indeed"], IndeedToggle)
    assert m.sources["indeed"].enabled is False


def test_old_jobspy_source_rejected():
    """The retired `sources.jobspy:` block fails the normal config validation —
    `jobspy` coerces to a bare SourceToggle (enable/disable only), so its old
    per-site / `jobs` payload trips `extra_forbidden`. The intended hard break,
    no special shim."""
    with pytest.raises(ValidationError):
        JobSearchPlugin(
            sources={
                "jobspy": {
                    "enabled": True,
                    "linkedin": True,
                    "indeed": True,
                    "jobs": {"results_wanted_per_query": 50},
                }
            }
        )


def test_indeed_toggle_rejects_extra():
    from daily_driver.plugins.job_search.config import IndeedToggle

    with pytest.raises(ValidationError):
        IndeedToggle(country_indeed="USA")  # old field name no longer valid


# ---------------------------------------------------------------------------
# PluginsConfig
# ---------------------------------------------------------------------------


def test_plugins_config_empty():
    m = PluginsConfig()
    assert m.job_search is None


def test_plugins_config_rejects_unknown_plugin():
    """PluginsConfig is extra='forbid': every registered plugin gets a typed
    field built from PLUGINS, so an unregistered namespace is a typo and errors."""
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


def test_config_rejects_unknown_plugin_namespace():
    """plugins is extra='forbid' so an unregistered namespace fails loudly,
    matching the strict root; a registered plugin (job_search) validates."""
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
    # The global default is the terminal fallback and keeps "claude"; per-task
    # provider defaults are now None ("unset") so the global can win the chain.
    assert ai.provider == "claude"
    assert ai.model is None
    assert ai.summary.provider is None
    assert ai.summary.model is None
    assert ai.voice_update.provider is None
    assert ai.ollama.endpoint == "http://localhost:11434"
    assert ai.ollama.timeout == 60


def test_ai_config_summary_ollama_with_model():
    from daily_driver.core.config_models import AIConfig

    ai = AIConfig.model_validate(
        {
            "summary": {"provider": "ollama", "model": "qwen2.5:14b"},
            "ollama": {"endpoint": "http://10.0.0.5:11434", "timeout": 120},
        }
    )
    assert ai.summary.provider == "ollama"
    assert ai.summary.model == "qwen2.5:14b"
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
    # Unset per-task/domain providers default to None; the global terminal
    # fallback ("claude") is what makes all-unset resolve to claude at runtime.
    assert c.ai.provider == "claude"
    assert c.ai.summary.provider is None
    assert EnrichmentConfig().provider is None


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


# ---------------------------------------------------------------------------
# AIConfig split: core keeps summary + provider blocks; enrichment moves to
# the job_search plugin (PART A of the enrichment-config split).
# ---------------------------------------------------------------------------


def test_core_ai_config_has_no_enrichment_task():
    """Core `ai:` no longer owns an enrichment task block."""
    from daily_driver.core.config_models import AIConfig

    assert "enrichment" not in AIConfig.model_fields


def test_core_ai_config_rejects_enrichment_key():
    """A stale `ai.enrichment:` block hard-fails (extra=forbid, no shim)."""
    from daily_driver.core.config_models import AIConfig

    with pytest.raises(ValidationError):
        AIConfig.model_validate({"enrichment": {"provider": "ollama"}})


def test_core_ai_config_constructor_rejects_enrichment_kwarg():
    """The migration's central promise: passing enrichment= at all is rejected.

    Locks the extra_forbidden break at the constructor, not only via
    model_validate, so a stray kwarg can't silently no-op.
    """
    from daily_driver.core.config_models import AIConfig, AITaskConfig

    with pytest.raises(ValidationError):
        AIConfig(enrichment=AITaskConfig())  # type: ignore[call-arg]


def test_core_config_rejects_ai_enrichment():
    """End-to-end: a root config with `ai.enrichment:` is rejected."""
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "tracker": {"categories": {"task": {"required": ["title"]}}},
                "ai": {"enrichment": {"provider": "ollama"}},
            }
        )


def test_core_ai_config_keeps_summary_and_providers():
    """Summary routing + claude/ollama connection blocks survive on core."""
    from daily_driver.core.config_models import AIConfig

    m = AIConfig()
    assert m.summary.provider is None
    assert m.provider == "claude"
    assert m.claude.max_parallel == 4
    assert m.ollama.endpoint == "http://localhost:11434"


def test_core_summary_can_route_to_ollama():
    """Summary may route to ollama (shared provider infra)."""
    from daily_driver.core.config_models import AIConfig

    m = AIConfig.model_validate({"summary": {"provider": "ollama", "model": "phi4"}})
    assert m.summary.provider == "ollama"
    assert m.summary.model == "phi4"


def test_plugin_enrichment_gains_provider_and_model():
    """EnrichmentConfig now carries its own provider/model routing."""
    m = EnrichmentConfig()
    assert m.provider is None
    assert m.model is None
    routed = EnrichmentConfig.model_validate(
        {"provider": "ollama", "model": "qwen2.5:14b"}
    )
    assert routed.provider == "ollama"
    assert routed.model == "qwen2.5:14b"


def test_plugin_enrichment_provider_rejects_unknown():
    with pytest.raises(ValidationError):
        EnrichmentConfig.model_validate({"provider": "gpt4"})


def test_plugin_enrichment_roundtrips_with_existing_knobs():
    """Provider/model coexist with the existing budget/timeout knobs."""
    m = EnrichmentConfig.model_validate(
        {
            "provider": "ollama",
            "model": "phi4",
            "enrich_timeout": 45,
            "max_enrich_fit": 10,
        }
    )
    assert m.provider == "ollama"
    assert m.enrich_timeout == 45
    assert m.max_enrich_fit == 10
