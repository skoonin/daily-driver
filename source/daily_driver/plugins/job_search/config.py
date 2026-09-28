"""Config models for the job_search plugin namespace (plugins.job_search)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Shared so the toggle field and the sources-block template comment stay in sync.
_WWR_CATEGORIES_DESC = (
    'Categories to read, e.g. "devops-sysadmin". This source needs one.'
)
# Greenhouse, Ashby and Lever pins are scraped in addition to the boards
# `jobs discover-boards` finds.
_GREENHOUSE_BOARDS_DESC = (
    "Boards always scraped: <slug> in boards.greenhouse.io/<slug>."
)
_ASHBY_BOARDS_DESC = (
    "Boards always scraped: <slug> in jobs.ashbyhq.com/<slug>. Case matters."
)
_LEVER_BOARDS_DESC = "Boards always scraped: <slug> in jobs.lever.co/<slug>."
_EXCLUDE_BOARDS_DESC = "Boards never scraped. Case matters."
_WORKABLE_ACCOUNTS_DESC = "Accounts scraped: <slug> in apply.workable.com/<slug>."
_WORKDAY_BOARDS_DESC = (
    "Sites as <tenant>.<host>.myworkdayjobs.com/<site>. `company` sets the name."
)
# The newest-100 feed is thin for any one role; tags surface relevant
# postings. An unknown tag returns the plain feed, which is deduped.
_REMOTEOK_TAGS_DESC = "Tags searched as well as the main feed."


class Locations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home_city: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "Vancouver, BC"},
    )
    remote: bool = Field(
        default=False,
        description="",
        json_schema_extra={"template_example": True},
    )
    remote_unlisted_countries: bool = Field(
        default=False,
        description=(
            "true keeps remote roles from any country. false keeps only those\n"
            "in `countries` or naming no country."
        ),
        json_schema_extra={"template_example": False},
    )
    countries: dict[str, list[str]] = Field(
        default={},
        description="Country code: allowed cities. An empty list allows the whole country.",
        json_schema_extra={
            "template_example": {"CA": ["Vancouver", "Victoria"], "US": []}
        },
    )

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

    enabled: bool = Field(default=True, description="")


class RemoteOkToggle(SourceToggle):
    """RemoteOK source toggle plus its per-source tag slug list."""

    remoteok_tags: list[str] = Field(default=[], description=_REMOTEOK_TAGS_DESC)

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

    wwr_categories: list[str] = Field(default=[], description=_WWR_CATEGORIES_DESC)


class GreenhouseToggle(SourceToggle):
    """Greenhouse source toggle plus its pinned/excluded board slug lists."""

    greenhouse_boards: list[str] = Field(
        default=[], description=_GREENHOUSE_BOARDS_DESC
    )
    exclude_boards: list[str] = Field(default=[], description=_EXCLUDE_BOARDS_DESC)


class AshbyToggle(SourceToggle):
    """AshbyHQ source toggle plus its pinned/excluded board slug lists."""

    ashby_boards: list[str] = Field(default=[], description=_ASHBY_BOARDS_DESC)
    exclude_boards: list[str] = Field(default=[], description=_EXCLUDE_BOARDS_DESC)


class LeverToggle(SourceToggle):
    """Lever source toggle plus its pinned/excluded board slug lists."""

    lever_boards: list[str] = Field(default=[], description=_LEVER_BOARDS_DESC)
    exclude_boards: list[str] = Field(default=[], description=_EXCLUDE_BOARDS_DESC)


class WorkableToggle(SourceToggle):
    """Workable source toggle plus its per-source account slug list."""

    workable_accounts: list[str] = Field(
        default=[], description=_WORKABLE_ACCOUNTS_DESC
    )


class WorkdayBoard(BaseModel):
    """One Workday careers site, named by the three parts of its URL.

    For ``companyh.wd5.myworkdayjobs.com/companyhcareers`` that is
    ``tenant=companyh``, ``host=wd5``, ``site=companyhcareers``. The
    Workday payload has no company field (the tenant is the company), so
    ``company`` is an optional display-name override; it defaults to the
    title-cased tenant.
    """

    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(description="The <tenant> in <tenant>.<host>.myworkdayjobs.com")
    host: str = Field(description="The <host> data-center segment, e.g. wd5")
    site: str = Field(description="The careers-site path segment, e.g. companyhcareers")
    company: str | None = Field(
        default=None,
        description="Display name for the company; defaults to the title-cased tenant.",
    )


class WorkdayToggle(SourceToggle):
    """Workday source toggle plus its per-source list of careers sites."""

    workday_boards: list[WorkdayBoard] = Field(
        default=[], description=_WORKDAY_BOARDS_DESC
    )


class HackerNewsToggle(SourceToggle):
    """HN source toggle plus its per-source post cap.

    Shared shape for both `hn_who_is_hiring` and `hn_jobs` — each owns its own
    `hn_max_posts` cap.
    """

    hn_max_posts: int = Field(default=500, description="")


# Each city in locations.countries gets its own search, so a local posting
# competes with its own city rather than the whole country. The cap bounds
# request volume: a throttled site returns nothing rather than an error.
# Skipped cities are reported, never dropped silently.
_MAX_CITY_QUERIES_DESC = "Most per-city searches in one run."


class LinkedInToggle(SourceToggle):
    """LinkedIn source toggle plus its query knobs.

    LinkedIn and Indeed are fetched via python-jobspy (an implementation
    detail); the user surface is the site name. ``scrape_jobs`` has no LinkedIn
    country parameter, so — unlike :class:`IndeedToggle` — there is no
    ``country`` knob here.
    """

    results_wanted_per_query: int = Field(default=50, description="")
    hours_old: int = Field(default=168, description="")
    max_city_queries: int = Field(default=40, description=_MAX_CITY_QUERIES_DESC)


class IndeedToggle(SourceToggle):
    """Indeed source toggle plus its query knobs.

    ``country`` maps to python-jobspy's ``country_indeed`` parameter and lives
    only here because that is the one ``scrape_jobs`` site it affects. It is the
    fallback used when a configured ISO country code is not in JobSpy's enum.
    """

    results_wanted_per_query: int = Field(default=50, description="")
    hours_old: int = Field(default=168, description="")
    country: str = Field(default="USA", description="")
    max_city_queries: int = Field(default=40, description=_MAX_CITY_QUERIES_DESC)


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

    enabled: bool = Field(
        default=False, description="true lets `jobs run` scrape. Off by default."
    )
    # Honors Retry-After, else exponential backoff (1.5s, 3s, 6s, ... up to 30s).
    max_retries: int = Field(
        default=3,
        description="Retries after HTTP 429 or 503. 0 disables.",
    )
    # hn_jobs searches 17k+ posts of history; other sources are recent anyway.
    max_age_days: int = Field(
        default=30,
        description="Skip hn_jobs postings older than this. 0 disables.",
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
            "Gecko/20100101 Firefox/128.0"
        ),
        description="User-Agent sent by the HTTP scrapers.",
    )
    # Board sources name a company after its board slug, so dropping these
    # words lets a `foosystems` board row match an aggregator's `Foo Systems
    # Inc.`. The stored Company value never changes.
    company_noise_words: list[str] = Field(
        default=[],
        description=(
            "Words ignored in company names when finding duplicates.\n"
            "Replaces the built-in list (inc, ltd, gmbh, labs, ...)."
        ),
        json_schema_extra={"inline_comment": '["inc", "ltd", "labs"]'},
    )
    timeout: int = Field(
        default=30,
        description="Seconds per source request.",
    )
    search_terms: list[str] | None = Field(
        default=None,
        description=(
            "Search terms for LinkedIn, Indeed and Apple. Unset builds them\n"
            "from `roles`."
        ),
    )
    # Overridden per scrape phase by the orchestrator (_ctx_with_headless), so a
    # user value has no effect; deliberately kept out of the generated template.
    headless: bool = Field(
        default=False, description="", json_schema_extra={"template_skip": True}
    )
    parallel_workers: int = Field(
        default=8,
        description="Sources scraped at the same time.",
    )
    max_pages: int = Field(
        default=3,
        description="Result pages read per Apple search term.",
    )
    browser: Literal["firefox", "chromium", "webkit"] = Field(
        default="firefox",
        description=(
            "Browser for Apple: firefox, chromium or webkit. Install it with\n"
            "`playwright install <browser>`."
        ),
    )


class Criterion(BaseModel):
    """One user-declared thing to look for in a job description.

    `assess` is a natural-language instruction the enrichment LLM reasons
    about (so polarity and negation survive), not a keyword to match.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="")
    assess: str = Field(description="")

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

    provider: Literal["claude", "ollama"] | None = Field(
        default=None,
        description="claude or ollama. Unset uses `enrichment.provider`.",
        json_schema_extra={"template_example": "claude"},
    )
    model: str | None = Field(
        default=None,
        description=(
            "Model for this pass. Unset uses `enrichment.model`, so set it\n"
            "when that model is for the other provider."
        ),
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )


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

    provider: Literal["claude", "ollama"] | None = Field(
        default=None,
        description="claude or ollama. Unset uses `ai.provider`.",
        json_schema_extra={"template_example": "claude"},
    )
    model: str | None = Field(
        default=None,
        description="Model for enrichment. Unset uses `ai.model`.",
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )
    fit_notes: PhaseRoute = Field(
        default=PhaseRoute(),
        description="The fit and notes pass (one call per job).",
    )
    enrich_timeout: int = Field(
        default=30,
        description="Seconds before one AI call is abandoned.",
    )
    enrich_fit: bool = Field(
        default=True,
        description="Score each job 1-10 for fit.",
    )
    enrich_notes: bool = Field(
        default=True,
        description="One-line Notes summary (stack, remote, red flags).",
    )
    enrich_is_remote: bool = Field(
        default=True,
        description="Mark jobs remote, hybrid or onsite.",
    )
    max_enrich_fit: int = Field(
        default=50,
        description="Most AI calls per run. Limits cost.",
    )
    # Pay, visa and EEO text usually sits at the end of a long posting, so a
    # low cap hides it from the criteria questions.
    max_description_words: int = Field(
        default=2000,
        ge=100,
        description="Words of each posting sent to the AI.",
    )
    # Lets an interrupted `backfill --force-update` resume where it stopped.
    force_recook_cooldown_hours: int | Literal["missing"] = Field(
        default=24,
        description=(
            "With `backfill --force-update`, skip rows enriched in the last N\n"
            "hours. 0 redoes every row; `missing` does only unenriched rows."
        ),
    )

    @field_validator("force_recook_cooldown_hours")
    @classmethod
    def _cooldown_non_negative(cls, v: int | str) -> int | str:
        if isinstance(v, int) and v < 0:
            raise ValueError("force_recook_cooldown_hours must be >= 0 or 'missing'")
        return v

    detail_delay_seconds: float = Field(
        default=0.5,
        description="Seconds between posting page fetches.",
    )
    criteria: list[Criterion] = Field(
        default=[],
        description='Questions asked of each posting. Each adds "Label: answer" to Notes.',
        json_schema_extra={
            "template_example": [
                {
                    "label": "Sponsorship",
                    "assess": "Does the role offer visa sponsorship?",
                }
            ]
        },
    )

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
    reverify_days: int = Field(
        default=7,
        description="Re-check a posting's link after this many days.",
    )
    # For rows with no checkable URL (Indeed's bot wall, HN permalinks).
    # Generous by design: a posting found again reopens loudly.
    unverified_age_days: int = Field(
        default=30,
        description="Close postings with no checkable link after this many days.",
    )

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
    reprobe_days: int = Field(
        default=30,
        description="Re-check a known board after this many days.",
    )
    # One sweep stamps every board on the same day, so an uncapped threshold
    # would re-probe the whole universe at once, every reprobe_days. Ranking
    # is by lateness against each board's own cadence, so dormant boards
    # cannot take every slot on the strength of an older timestamp.
    max_reprobe_per_sweep: int = Field(
        default=500,
        description="Most boards re-checked per sweep, most overdue first.",
    )
    # Below the ratio the run logs the success rate instead: a few
    # permanently-gone 404s are not an outage, and a warning on every run
    # trains the reader to ignore it.
    degraded_failure_ratio: float = Field(
        default=0.25,
        description="Share of a platform's boards that must fail to report it degraded.",
    )
    # A board that lists nothing has likely moved ATS; one listing jobs you
    # don't match is alive and keeps the normal cadence. One non-empty probe
    # resets the streak.
    dormant_after_empty_sweeps: int = Field(
        default=2,
        description="Empty checks in a row before a board is checked less often. 0 disables.",
    )
    # Dormancy defers a re-probe, never cancels it: --full still re-probes
    # every dormant board, and a pinned board is always scraped. 1 keeps the
    # normal cadence but still reports dormant boards.
    dormant_reprobe_multiplier: int = Field(
        default=6,
        description="A dormant board is re-checked every reprobe_days x this.",
    )

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
    enabled: bool = Field(
        default=True,
        description="false turns job search off and keeps these settings.",
        # The plugins section writes `enabled: true` itself, uncommented.
        json_schema_extra={"template_skip": True},
    )
    persona: str | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example": "Senior SRE / Platform Engineer"},
    )
    roles: list[str] = Field(
        default=[],
        description="",
        json_schema_extra={"inline_comment": '["SRE", "Platform Engineer"]'},
    )
    domain_keywords: list[str] = Field(
        default=[],
        description="",
        json_schema_extra={"inline_comment": '["kubernetes", "observability"]'},
    )
    seniority_keywords: list[str] = Field(
        default=[],
        description="",
        json_schema_extra={"inline_comment": '["senior", "staff", "principal"]'},
    )
    locations: Locations | None = Field(
        default=None,
        description="",
        json_schema_extra={"template_example_model": True},
    )
    scraper: ScraperConfig = Field(
        default=ScraperConfig(),
        description="",
    )
    enrichment: EnrichmentConfig = Field(
        default=EnrichmentConfig(),
        description="AI pass that scores fit and writes Notes. Costs API calls.",
    )
    # Board-backed rows are verified by board diff on every run instead.
    verify: VerifyConfig = Field(
        default=VerifyConfig(),
        description="`jobs verify`: closes postings that are gone.",
    )
    discovery: DiscoveryConfig = Field(
        default=DiscoveryConfig(),
        description="`jobs discover-boards`: finds company job boards.",
    )
    sources: dict[str, SourceToggle] = Field(
        default_factory=dict,
        description="",
        json_schema_extra={
            "template_example": {
                "remoteok": {
                    "enabled": False,
                    "remoteok_tags": ["devops", "kubernetes", "aws"],
                },
                "weworkremotely": {"enabled": False, "wwr_categories": []},
                "hn_who_is_hiring": {"enabled": False, "hn_max_posts": 500},
                "hn_jobs": {"enabled": False, "hn_max_posts": 500},
                "greenhouse": {
                    "enabled": False,
                    "greenhouse_boards": [],
                    "exclude_boards": [],
                },
                "ashby": {
                    "enabled": False,
                    "ashby_boards": [],
                    "exclude_boards": [],
                },
                "lever": {
                    "enabled": False,
                    "lever_boards": [],
                    "exclude_boards": [],
                },
                "workable": {"enabled": False, "workable_accounts": []},
                "workday": {
                    "enabled": False,
                    "workday_boards": [
                        {
                            "tenant": "companyh",
                            "host": "wd5",
                            "site": "companyhcareers",
                            "company": "Company H",
                        }
                    ],
                },
                "apple": False,
                "linkedin": {
                    "enabled": True,
                    "results_wanted_per_query": 50,
                    "hours_old": 168,
                    "max_city_queries": 40,
                },
                "indeed": {
                    "enabled": True,
                    "results_wanted_per_query": 50,
                    "hours_old": 168,
                    "country": "USA",
                    "max_city_queries": 40,
                },
            },
            "template_example_field_comments": {
                "remoteok": {"remoteok_tags": _REMOTEOK_TAGS_DESC},
                "weworkremotely": {"wwr_categories": _WWR_CATEGORIES_DESC},
                "greenhouse": {
                    "greenhouse_boards": _GREENHOUSE_BOARDS_DESC,
                    "exclude_boards": _EXCLUDE_BOARDS_DESC,
                },
                "ashby": {
                    "ashby_boards": _ASHBY_BOARDS_DESC,
                    "exclude_boards": _EXCLUDE_BOARDS_DESC,
                },
                "lever": {
                    "lever_boards": _LEVER_BOARDS_DESC,
                    "exclude_boards": _EXCLUDE_BOARDS_DESC,
                },
                "workable": {"workable_accounts": _WORKABLE_ACCOUNTS_DESC},
                "workday": {"workday_boards": _WORKDAY_BOARDS_DESC},
            },
            "template_example_inline_comments": {
                "remoteok": "remoteok.com",
                "weworkremotely": "weworkremotely.com",
                "hn_who_is_hiring": 'HN monthly "Who is hiring?" thread',
                "hn_jobs": "HN jobs (YC companies)",
                "greenhouse": "Greenhouse boards",
                "ashby": "Ashby boards",
                "lever": "Lever boards",
                "workable": "Workable accounts",
                "workday": "Workday careers sites",
                "apple": "jobs.apple.com",
                "indeed": "`country` sets the regional site",
            },
            "template_example_block_comments": {
                "remoteok": (
                    "Only listed sources run. Inside a source, a key left out\n"
                    "uses its default."
                ),
                "apple": "Needs a browser: `playwright install`.",
            },
        },
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
