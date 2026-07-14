"""Config models for the job_search plugin namespace (plugins.job_search)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Shared so the toggle field and the sources-block template comment stay in sync.
_WWR_CATEGORIES_DESC = (
    'Category slugs, e.g. "devops-sysadmin" ->\n'
    "/categories/remote-devops-sysadmin-jobs.rss. Empty scrapes nothing;\n"
    "this source needs at least one."
)
_GREENHOUSE_BOARDS_DESC = (
    'Slugs always scraped (the "<slug>" in boards.greenhouse.io/<slug>),\n'
    "unioned with the discover-boards matched cache."
)
_ASHBY_BOARDS_DESC = (
    'Slugs always scraped (the "<slug>" in jobs.ashbyhq.com/<slug>),\n'
    "unioned with the discover-boards matched cache. Case-sensitive."
)
_LEVER_BOARDS_DESC = (
    'Slugs always scraped (the "<slug>" in jobs.lever.co/<slug>),\n'
    "unioned with the discover-boards matched cache."
)
_EXCLUDE_BOARDS_DESC = (
    "Slugs NEVER scraped, even if pinned or discovered. Matched exactly\n"
    "incl. case: to silence Notion, write Notion not notion."
)
_WORKABLE_ACCOUNTS_DESC = (
    'Account slugs (the "<slug>" in apply.workable.com/<slug>). Each\n'
    "account's full list is fetched in one request."
)
_WORKDAY_BOARDS_DESC = (
    "Each site named by the three URL parts. For\n"
    "crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers that is\n"
    "tenant=crowdstrike, host=wd5, site=crowdstrikecareers. Optional `company`\n"
    "overrides the display name (default: title-cased tenant)."
)
_REMOTEOK_TAGS_DESC = (
    "Tag slugs queried alongside the unfiltered feed, each as\n"
    "remoteok.com/api?tags=<slug>. The newest-100 feed is thin per role, so\n"
    "tags surface relevant postings. Unknown tags harmlessly return the\n"
    "unfiltered feed (deduped). Empty = feed only."
)


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
            "When true, remote roles from ANY country pass (country-blind). When\n"
            "false (default), a remote role passes only if its location names a\n"
            "country in `countries` (or names no country at all); a remote role\n"
            "that names an unlisted country is dropped."
        ),
        json_schema_extra={"template_example": False},
    )
    countries: dict[str, list[str]] = Field(
        default={},
        description=(
            "ISO code -> city allow-list. Empty list = anywhere in that country;\n"
            "a non-empty list narrows to ONLY those cities. Remote roles pass when\n"
            "`remote` is true, scoped to these countries unless\n"
            "`remote_unlisted_countries` is set."
        ),
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
        default=["anthropic"], description=_GREENHOUSE_BOARDS_DESC
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

    For ``crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers`` that is
    ``tenant=crowdstrike``, ``host=wd5``, ``site=crowdstrikecareers``. The
    Workday payload has no company field (the tenant is the company), so
    ``company`` is an optional display-name override; it defaults to the
    title-cased tenant.
    """

    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(description="The <tenant> in <tenant>.<host>.myworkdayjobs.com")
    host: str = Field(description="The <host> data-center segment, e.g. wd5")
    site: str = Field(
        description="The careers-site path segment, e.g. crowdstrikecareers"
    )
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


class LinkedInToggle(SourceToggle):
    """LinkedIn source toggle plus its query knobs.

    LinkedIn and Indeed are fetched via python-jobspy (an implementation
    detail); the user surface is the site name. ``scrape_jobs`` has no LinkedIn
    country parameter, so — unlike :class:`IndeedToggle` — there is no
    ``country`` knob here.
    """

    results_wanted_per_query: int = Field(default=50, description="")
    hours_old: int = Field(default=168, description="")


class IndeedToggle(SourceToggle):
    """Indeed source toggle plus its query knobs.

    ``country`` maps to python-jobspy's ``country_indeed`` parameter and lives
    only here because that is the one ``scrape_jobs`` site it affects. It is the
    fallback used when a configured ISO country code is not in JobSpy's enum.
    """

    results_wanted_per_query: int = Field(default=50, description="")
    hours_old: int = Field(default=168, description="")
    country: str = Field(default="USA", description="")


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

    enabled: bool = Field(default=False, description="")
    max_retries: int = Field(
        default=3,
        description=(
            "Retries for HTTP 429/503. Honors Retry-After, else exponential\n"
            "backoff (1.5s, 3s, 6s, ... capped at 30s). 0 disables."
        ),
    )
    max_age_days: int = Field(
        default=30,
        description=(
            "Max posting age in days. Applied to hn_jobs (17k+ Algolia history);\n"
            "other sources are implicitly recent. 0 disables."
        ),
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
            "Gecko/20100101 Firefox/128.0"
        ),
        description="User-Agent header sent by the HTTP/RSS scrapers.",
    )
    timeout: int = Field(
        default=30,
        description="Per-source HTTP / browser timeout, in seconds.",
    )
    search_terms: list[str] | None = Field(
        default=None,
        description=(
            "Override search terms for query-based sources (JobSpy, Apple).\n"
            "Unset derives them from roles and keywords."
        ),
    )
    # Overridden per scrape phase by the orchestrator (_ctx_with_headless), so a
    # user value has no effect; deliberately kept out of the generated template.
    headless: bool = Field(
        default=False, description="", json_schema_extra={"template_skip": True}
    )
    parallel_workers: int = Field(
        default=8,
        description="Worker threads for the parallel (headless) scrape phase.",
    )
    max_pages: int = Field(
        default=3,
        description="Pagination / scroll passes per search term (Apple, JobSpy).",
    )
    browser: Literal["firefox", "chromium", "webkit"] = Field(
        default="firefox",
        description=(
            "Playwright engine for browser sources (Apple): firefox, chromium,\n"
            "or webkit. Must be installed (`playwright install <engine>`)."
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
        description=(
            "claude | ollama. Unset inherits the enrichment domain default,\n"
            "then `ai.provider`, then claude."
        ),
        json_schema_extra={"template_example": "claude"},
    )
    model: str | None = Field(
        default=None,
        description=(
            'Provider-specific model id (claude: "sonnet"; ollama: a tag like\n'
            '"qwen2.5:7b"). Chosen independently of provider, so it must match\n'
            "whichever provider this phase resolves to."
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
        description=(
            "Domain default provider for enrichment. Unset inherits `ai.provider`,\n"
            "then claude. The per-phase block overrides it."
        ),
        json_schema_extra={"template_example": "claude"},
    )
    model: str | None = Field(
        default=None,
        description=(
            'Provider-specific model id (claude: "sonnet"; ollama: a tag like\n'
            '"qwen2.5:14b"). Chosen independently of provider, so it must match\n'
            "whichever provider a phase resolves to."
        ),
        json_schema_extra={"template_commented": True, "template_example": "sonnet"},
    )
    fit_notes: PhaseRoute = Field(
        default=PhaseRoute(),
        description="Route override for the fit/notes pass (one call per job).",
    )
    enrich_timeout: int = Field(
        default=30,
        description="Seconds before a single enrichment LLM call is abandoned.",
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
        description="Judge remote/hybrid/onsite (no extra LLM call).",
    )
    max_enrich_fit: int = Field(
        default=50,
        description="Cap on fit/notes LLM calls per run (bounds API cost).",
    )
    force_recook_cooldown_hours: int | Literal["missing"] = Field(
        default=24,
        description=(
            "Under `backfill --force-update`, skip rows enriched within N hours\n"
            "so an interrupted run resumes. 0 = re-enrich every active row;\n"
            "`missing` = only rows with no enrichment timestamp yet."
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
        description="Pause between detail-page fetches, in seconds.",
    )
    criteria: list[Criterion] = Field(
        default=[],
        description=(
            "Extra assessments per job description. Each appends a\n"
            '"Label: value" to Notes. `assess` is a natural-language instruction\n'
            "the LLM reasons about, not a keyword match."
        ),
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

    reverify_days: int = Field(
        default=7,
        description=(
            "Re-check a row's URL when its last liveness evidence (Date\n"
            "Verified, else Date Found) is at least this many days old."
        ),
    )
    unverified_age_days: int = Field(
        default=30,
        description=(
            "Rows with no checkable URL (indeed bot wall, HN permalinks) close as\n"
            "age-unverified once last evidence (Date Verified, refreshed by\n"
            "re-sightings, else Date Found) is this old. Generous by design; a\n"
            "re-found posting reopens loudly."
        ),
    )

    @field_validator("reverify_days", "unverified_age_days")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("verify day thresholds must be >= 1")
        return value


class JobSearchPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
        description=(
            "Post-scrape LLM pass adding a fit score and Notes summary. Costs\n"
            "API calls; max_enrich_fit caps how many run."
        ),
    )
    verify: VerifyConfig = Field(
        default=VerifyConfig(),
        description=(
            "`jobs verify` liveness checks for sources without full board\n"
            "listings (board-backed rows verify by board-diff each run)."
        ),
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
                    "greenhouse_boards": ["anthropic"],
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
                            "tenant": "crowdstrike",
                            "host": "wd5",
                            "site": "crowdstrikecareers",
                            "company": "CrowdStrike",
                        }
                    ],
                },
                "apple": False,
                "linkedin": {
                    "enabled": True,
                    "results_wanted_per_query": 50,
                    "hours_old": 168,
                },
                "indeed": {
                    "enabled": True,
                    "results_wanted_per_query": 50,
                    "hours_old": 168,
                    "country": "USA",
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
                "remoteok": "remoteok.com RSS",
                "weworkremotely": "weworkremotely.com listings",
                "hn_who_is_hiring": 'HN monthly "Who is hiring?" thread',
                "hn_jobs": "HN curated jobs (YC-funded company posts)",
                "greenhouse": "Greenhouse boards (configurable list)",
                "ashby": "AshbyHQ boards (configurable list)",
                "lever": "Lever boards (configurable list)",
                "workable": "Workable accounts (configurable list)",
                "workday": "Workday careers sites (configurable list)",
                "apple": "jobs.apple.com (API intercept)",
                "indeed": "indeed.com (country sets the regional host)",
            },
            "template_example_block_comments": {
                "remoteok": (
                    "All keys optional; omitted = enabled (SourceToggle default).\n"
                    "Set a key to `false` to disable it.\n"
                    "\n"
                    "Shipped scrapers (HTTP/RSS, no extra deps):"
                ),
                "apple": ("Shipped scrapers (Playwright, needs `playwright install`):"),
                "linkedin": (
                    "LinkedIn + Indeed (JobSpy, needs python-jobspy>=1.1.82). Each\n"
                    "site is fetched separately under its own progress row.\n"
                    "`country` (Indeed only) sets the regional host and is the\n"
                    "fallback for unrecognized countries."
                ),
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
