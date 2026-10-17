"""Config models for the job_search plugin namespace (plugins.job_search)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Locations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home_city: str | None = None
    remote: bool = False
    remote_unlisted_countries: bool = False
    countries: dict[str, list[str]] = {}

    @field_validator("countries")
    @classmethod
    def _no_blank_cities(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        # A blank city compiles to a zero-width match in the location filter
        # and silently accepts nearly everything — the exact opposite of
        # narrowing. Reject loudly instead of guessing intent.
        for code, cities in value.items():
            if any(not city.strip() for city in cities):
                raise ValueError(f"countries.{code} contains a blank city entry")
        return value


class SourceToggle(BaseModel):
    """Per-source enable/disable plus optional source-specific options."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class RemoteOkToggle(SourceToggle):
    """RemoteOK source toggle plus its per-source tag slug list."""

    # The newest-100 feed is thin for any one role; tags surface relevant
    # postings. An unknown tag returns the plain feed, which is deduped.
    remoteok_tags: list[str] = []

    @field_validator("remoteok_tags")
    @classmethod
    def _normalize_tags(cls, value: list[str]) -> list[str]:
        # RemoteOK tag slugs are lowercase; a blank entry would build a
        # zero-value ?tags= query that just returns the unfiltered feed, so
        # drop blanks and dedupe while preserving order.
        seen: set[str] = set()
        normalized: list[str] = []
        for tag in value:
            slug = tag.strip().lower()
            if slug and slug not in seen:
                seen.add(slug)
                normalized.append(slug)
        return normalized


class WeWorkRemotelyToggle(SourceToggle):
    """WeWorkRemotely source toggle plus its per-source category list."""

    wwr_categories: list[str] = []


# Greenhouse, Ashby and Lever pins are scraped in addition to the boards
# `jobs discover-boards` finds.
class GreenhouseToggle(SourceToggle):
    """Greenhouse source toggle plus its pinned/excluded board slug lists."""

    greenhouse_boards: list[str] = []
    exclude_boards: list[str] = []


class AshbyToggle(SourceToggle):
    """AshbyHQ source toggle plus its pinned/excluded board slug lists."""

    ashby_boards: list[str] = []
    exclude_boards: list[str] = []


class LeverToggle(SourceToggle):
    """Lever source toggle plus its pinned/excluded board slug lists."""

    lever_boards: list[str] = []
    exclude_boards: list[str] = []


class WorkableToggle(SourceToggle):
    """Workable source toggle plus its per-source account slug list."""

    workable_accounts: list[str] = []


class WorkdayBoard(BaseModel):
    """One Workday careers site, named by the three parts of its URL.

    For ``companyh.wd5.myworkdayjobs.com/companyhcareers`` that is
    ``tenant=companyh``, ``host=wd5``, ``site=companyhcareers``. The
    Workday payload has no company field (the tenant is the company), so
    ``company`` is an optional display-name override; it defaults to the
    title-cased tenant.
    """

    model_config = ConfigDict(extra="forbid")

    tenant: str
    host: str
    site: str
    company: str | None = None


class WorkdayToggle(SourceToggle):
    """Workday source toggle plus its per-source list of careers sites."""

    workday_boards: list[WorkdayBoard] = []


class HackerNewsToggle(SourceToggle):
    """HN source toggle plus its per-source post cap.

    Shared shape for both `hn_who_is_hiring` and `hn_jobs` — each owns its own
    `hn_max_posts` cap.
    """

    hn_max_posts: int = 500


class LinkedInToggle(SourceToggle):
    """LinkedIn source toggle plus its query knobs.

    LinkedIn and Indeed are fetched via python-jobspy (an implementation
    detail); the user surface is the site name. ``scrape_jobs`` has no LinkedIn
    country parameter, so — unlike :class:`IndeedToggle` — there is no
    ``country`` knob here.
    """

    results_wanted_per_query: int = 50
    hours_old: int = 168
    # Each city in locations.countries gets its own search per search term, so
    # a local posting competes with its own city rather than the whole
    # country. The cap counts those searches and bounds request volume: a
    # throttled site returns nothing rather than an error. The run log reports
    # how many searches the cap skipped. Same for Indeed.
    max_city_queries: int = 40


class IndeedToggle(SourceToggle):
    """Indeed source toggle plus its query knobs.

    ``country`` maps to python-jobspy's ``country_indeed`` parameter and lives
    only here because that is the one ``scrape_jobs`` site it affects. It is the
    fallback used when a configured ISO country code is not in JobSpy's enum.
    """

    results_wanted_per_query: int = 50
    hours_old: int = 168
    country: str = "USA"
    max_city_queries: int = 40


# Maps a source key to the SourceToggle subclass that carries its per-source
# knobs. Keys absent here coerce to the bare SourceToggle (enable/disable only).
_SOURCE_TOGGLE_TYPES: dict[str, type[SourceToggle]] = {
    "remoteok": RemoteOkToggle,
    "weworkremotely": WeWorkRemotelyToggle,
    "greenhouse": GreenhouseToggle,
    "ashby": AshbyToggle,
    "lever": LeverToggle,
    "workable": WorkableToggle,
    "workday": WorkdayToggle,
    "hn_who_is_hiring": HackerNewsToggle,
    "hn_jobs": HackerNewsToggle,
    "linkedin": LinkedInToggle,
    "indeed": IndeedToggle,
}


class ScraperConfig(BaseModel):
    """Transport / retry knobs shared by every scraper source."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Honors Retry-After, else exponential backoff (1.5s, 3s, 6s, ... up to 30s).
    max_retries: int = 3
    # hn_jobs searches 17k+ posts of history; other sources are recent anyway.
    max_age_days: int = 30
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    )
    # Board sources name a company after its board slug, so dropping these
    # words lets a `foosystems` board row match an aggregator's `Foo Systems
    # Inc.`. The stored Company value never changes.
    company_noise_words: list[str] = []
    timeout: int = 30
    search_terms: list[str] | None = None
    # Overridden per scrape phase by the orchestrator (_ctx_with_headless), so a
    # user value has no effect; deliberately left out of the config template.
    headless: bool = False
    parallel_workers: int = 8
    max_pages: int = 3
    browser: Literal["firefox", "chromium", "webkit"] = "firefox"


class Criterion(BaseModel):
    """One user-declared thing to look for in a job description.

    `assess` is a natural-language instruction the enrichment LLM reasons
    about (so polarity and negation survive), not a keyword to match.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    assess: str

    @field_validator("label", "assess")
    @classmethod
    def _single_line_non_empty(cls, value: str) -> str:
        # label rides the prompt and the "Label: value" Notes segment; newlines
        # would break the single-line Notes/CSV invariant.
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        if "\n" in cleaned or "\r" in cleaned:
            raise ValueError("must be a single line")
        return cleaned


class PhaseRoute(BaseModel):
    """Per-phase AI provider/model override for one enrichment pass.

    A local 2-field mirror of core's `AITaskConfig`, defined here rather than
    imported: `core/config_models.py` eagerly imports the plugin config chain
    before `AITaskConfig` is bound, so importing it from core would raise an
    ImportError at startup. Plugins never import core (see module docstring of
    `plugins/config.py`). Both fields default to None (= "inherit from the
    resolution chain"); the resolver supplies the terminal claude fallback.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] | None = None
    model: str | None = None


class EnrichmentConfig(BaseModel):
    """Knobs for the post-scrape enrichment pass (comp, fit, notes).

    Owns its own AI provider routing: enrichment is plugin-specific, so the
    provider/model selection lives here rather than leaking into core's `ai:`
    block. The provider-connection blocks (parallelism, ollama endpoint /
    timeout) are still shared core infra under `ai.claude:` / `ai.ollama:`.
    The single LLM pass — `fit_notes` (fit / notes / remote / criteria) — takes
    an optional per-phase route block that overrides these domain defaults.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "ollama"] | None = None
    model: str | None = None
    fit_notes: PhaseRoute = PhaseRoute()
    enrich_timeout: int = 30
    enrich_fit: bool = True
    enrich_notes: bool = True
    enrich_is_remote: bool = True
    max_enrich_fit: int = 50
    # Pay, visa and EEO text usually sits at the end of a long posting, so a
    # low cap hides it from the criteria questions.
    max_description_words: int = Field(
        default=2000,
        ge=100,
    )
    # Lets an interrupted `backfill --force-update` resume where it stopped.
    force_recook_cooldown_hours: int | Literal["missing"] = 24

    @field_validator("force_recook_cooldown_hours")
    @classmethod
    def _cooldown_non_negative(cls, v: int | str) -> int | str:
        if isinstance(v, int) and v < 0:
            raise ValueError("force_recook_cooldown_hours must be >= 0 or 'missing'")
        return v

    detail_delay_seconds: float = 0.5
    criteria: list[Criterion] = []

    @field_validator("criteria")
    @classmethod
    def _unique_labels(cls, value: list[Criterion]) -> list[Criterion]:
        # The LLM keys its response object by label; duplicates collapse to one
        # value yet would fold multiple times into Notes.
        labels = [c.label for c in value]
        if len(labels) != len(set(labels)):
            raise ValueError("criteria labels must be unique")
        return value


class VerifyConfig(BaseModel):
    """Knobs for `jobs verify` liveness checks (url-check + age fallback)."""

    model_config = ConfigDict(extra="forbid")

    # Age is counted from Date Verified, else Date Found.
    reverify_days: int = 7
    # For rows with no checkable URL (Indeed's bot wall, HN permalinks).
    # Generous by design: a posting found again reopens loudly.
    unverified_age_days: int = 30

    @field_validator("reverify_days", "unverified_age_days")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("verify day thresholds must be >= 1")
        return value


class DiscoveryConfig(BaseModel):
    """Knobs for the `jobs discover-boards` sweep and board-health reporting."""

    model_config = ConfigDict(extra="forbid")

    # Without re-probing, an incremental sweep only probes new slugs, so a
    # board that dies after being matched stays in the scrape list until a
    # --full sweep.
    reprobe_days: int = 30
    # One sweep stamps every board on the same day, so an uncapped threshold
    # would re-probe the whole universe at once, every reprobe_days. Ranking
    # is by lateness against each board's own cadence, so dormant boards
    # cannot take every slot on the strength of an older timestamp.
    max_reprobe_per_sweep: int = 500
    # Below the ratio the run logs the success rate instead: a few
    # permanently-gone 404s are not an outage, and a warning on every run
    # trains the reader to ignore it.
    degraded_failure_ratio: float = 0.25
    # A board that lists nothing has likely moved ATS; one listing jobs you
    # don't match is alive and keeps the normal cadence. One non-empty probe
    # resets the streak.
    dormant_after_empty_sweeps: int = 2
    # Dormancy defers a re-probe, never cancels it: --full still re-probes
    # every dormant board, and a pinned board is always scraped. 1 keeps the
    # normal cadence but still reports dormant boards.
    dormant_reprobe_multiplier: int = 6

    @field_validator("reprobe_days", "max_reprobe_per_sweep")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("reprobe_days and max_reprobe_per_sweep must be >= 1")
        return value

    @field_validator("dormant_after_empty_sweeps")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("dormant_after_empty_sweeps must be >= 0")
        return value

    @field_validator("dormant_reprobe_multiplier")
    @classmethod
    def _at_least_one(cls, value: int) -> int:
        # 1 is a legal no-op. 0 would mean "never re-probe", which is
        # retirement, not dormancy -- the dead cache owns permanence.
        if value < 1:
            raise ValueError("dormant_reprobe_multiplier must be >= 1")
        return value

    @field_validator("degraded_failure_ratio")
    @classmethod
    def _fraction(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError("degraded_failure_ratio must be > 0 and <= 1")
        return value


class JobSearchPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Defaults on: having the block at all is the opt-in, so existing configs
    # keep working. `enabled: false` switches job search off and keeps settings.
    enabled: bool = True
    persona: str | None = None
    roles: list[str] = []
    domain_keywords: list[str] = []
    seniority_keywords: list[str] = []
    locations: Locations | None = None
    scraper: ScraperConfig = ScraperConfig()
    enrichment: EnrichmentConfig = EnrichmentConfig()
    # Board-backed rows are verified by board diff on every run instead.
    verify: VerifyConfig = VerifyConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    sources: dict[str, SourceToggle] = Field(
        default_factory=dict,
    )

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        out: dict[str, Any] = {}
        for key, val in v.items():
            toggle_cls = _SOURCE_TOGGLE_TYPES.get(key, SourceToggle)
            if isinstance(val, bool):
                out[key] = toggle_cls(enabled=val)
            elif isinstance(val, dict):
                out[key] = toggle_cls.model_validate(val)
            else:
                out[key] = val
        return out
