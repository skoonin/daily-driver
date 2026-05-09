"""
Job scraper: scrapes enabled sources and appends new rows to jobs.csv.
Safe to re-run — deduplicates by URL and by (company, role) against existing rows.

Sources (API — no browser required):
  remoteok          — requests, public JSON API (/api)
  weworkremotely    — requests, public RSS feeds (/categories/*.rss)
  hn_who_is_hiring  — requests + BeautifulSoup (static HTML)
  greenhouse        — requests, public JSON API (boards-api.greenhouse.io)
  jobspy            — python-jobspy library (LinkedIn, Indeed, Glassdoor, Google)

Sources (Playwright — browser required):
  wellfound         — Playwright, public search (non-headless, best-effort)
  apple             — Playwright, search input + API response intercept
"""

import copy
import logging
import re
import shutil
import subprocess
import sys
import time
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daily_driver.scraper.models import (
        NormalizedJob,
        RawScrapedJob,
    )

if sys.platform != "win32":
    pass

import requests
import yaml
from bs4 import XMLParsedAsHTMLWarning

from daily_driver.core.clock import today
from daily_driver.core.config_models import JobSearchPlugin, Locations, ScraperConfig


class ScraperError(RuntimeError):
    """Raised on unrecoverable scraper errors.

    Library code raises this instead of calling sys.exit() so the CLI layer
    can decide how to report and exit.
    """


from daily_driver.scraper.comp import (  # noqa: E402,F401
    _COMP_CURRENCY_PREFIX,
    _COMP_UNIT_SUFFIX,
    _format_comp,
    _parse_comp,
    _to_int,
    comp_meets_threshold,
    currency_matches_primary,
)
from daily_driver.scraper.csv_io import (  # noqa: E402,F401
    _CSV_TO_DICT,
    _DICT_TO_CSV,
    _PLACEHOLDER_PRODUCT,
    CANONICAL_HEADER,
    _dict_to_enriched_updates,
    _dict_to_row,
    _enriched_to_dict,
    _migrate_legacy_header,
    _row_to_dict,
    append_jobs,
    append_jobs_typed,
    backfill,
    load_existing_jobs,
)
from daily_driver.scraper.enrichment import (  # noqa: E402,F401
    _location_summary,
    enrich_company_descriptions,
    enrich_company_descriptions_typed,
    enrich_fit_and_notes,
    enrich_fit_and_notes_typed,
    enrich_job_details,
    enrich_job_details_typed,
)
from daily_driver.scraper.parsing import (  # noqa: E402,F401
    _find_jobposting,
    _fix_mojibake,
    _parse_detail_page,
    parse_greenhouse_html,
    parse_jsonld_jobposting,
)
from daily_driver.scraper.sources._http import (  # noqa: E402,F401
    COUNTRY_MAP,
    COUNTRY_NAMES,
    _api_get,
    _has_playwright,
    _http_session,
    _playwright_browser,
    country_params,
)
from daily_driver.scraper.sources.apple import scrape_apple  # noqa: E402,F401
from daily_driver.scraper.sources.greenhouse import scrape_greenhouse  # noqa: E402,F401
from daily_driver.scraper.sources.hn_who_is_hiring import (  # noqa: E402,F401
    scrape_hn_who_is_hiring,
)
from daily_driver.scraper.sources.jobspy import (  # noqa: E402,F401
    _format_jobspy_comp,
    _jobspy_str,
    jobspy_row_to_raw,
    normalize_jobspy_row,
    scrape_jobspy,
)
from daily_driver.scraper.sources.remoteok import scrape_remoteok  # noqa: E402,F401
from daily_driver.scraper.sources.wellfound import scrape_wellfound  # noqa: E402,F401
from daily_driver.scraper.sources.weworkremotely import (  # noqa: E402,F401
    scrape_weworkremotely,
)

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

log = logging.getLogger(__name__)

# Two-tier role matching: domain + optional seniority.
# Defaults used when config keys are absent.
_DEFAULT_DOMAIN_KEYWORDS = {
    "sre",
    "site reliability",
    "platform engineer",
    "platform engineering",
    "devops",
    "infrastructure",
    "cloud engineer",
    "cloud engineering",
    "ci/cd",
    "build engineer",
    "release engineer",
    "production engineer",
}
_DEFAULT_SENIORITY_KEYWORDS = {"senior", "staff", "principal", "lead", "sr.", "sr "}

# Seniority prefixes stripped when compressing 21 roles → ~10 search terms
_SENIORITY_PREFIXES = (
    "senior ",
    "staff ",
    "principal ",
    "lead ",
    "sr. ",
    "sr ",
)


# ── Config ────────────────────────────────────────────────────────────────────


def load_config(config_path: Path) -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as f:
        loaded: dict[str, Any] = yaml.safe_load(f) or {}
        return loaded


# ── Config model helpers ─────────────────────────────────────────────────────
#
# The scraper receives a raw dict (shape: {"job_search": {...}}) from the CLI
# so the tests can exercise it with literal dicts without constructing pydantic
# models. We lift that dict into JobSearchPlugin once at each call site —
# pydantic applies defaults, rejects unknown keys, and gives us typed attribute
# access instead of the old .get("key", default) chain.


def _model(config: dict[str, Any] | None) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate((config or {}).get("job_search") or {})


def validate_config(config: dict[str, Any] | None) -> None:
    """Fail fast on an invalid config before any scraping begins.

    JobSearchPlugin has extra='forbid', so a typo'd field under job_search
    would otherwise crash inside an enrichment loop after minutes of work.
    Call once at run/backfill entry.
    """
    _model(config)


def min_comp_usd(config: dict[str, Any]) -> int:
    """Compensation floor (USD) for the comp-threshold filter."""
    return _model(config).min_comp_usd


def scraper_cfg(config: dict[str, Any] | None) -> ScraperConfig:
    """Return the ScraperConfig pydantic model (with all defaults applied)."""
    return _model(config).scraper


def roles_list(config: dict[str, Any]) -> list[str]:
    return list(_model(config).roles)


def user_agent(config: dict[str, Any]) -> str:
    return _model(config).scraper.user_agent


def timeout_seconds(config: dict[str, Any]) -> int:
    return _model(config).scraper.timeout


def enrich_timeout(config: dict[str, Any] | None) -> int:
    """Timeout for Claude CLI enrichment calls (seconds)."""
    return _model(config).scraper.enrich_timeout


def locations_config(config: dict[str, Any]) -> Locations | None:
    """Return the job_search.locations block as a Locations model, or None."""
    return _model(config).locations


def persona(config: dict[str, Any]) -> str:
    """Return the job_search.persona string for enrichment prompts."""
    return _model(config).persona or "SRE/Platform/Infra engineer"


def home_city(config: dict[str, Any]) -> str:
    """Return the configured home city from locations block."""
    loc = locations_config(config)
    return (loc.home_city if loc and loc.home_city else None) or "Vancouver, BC"


def domain_keywords(config: dict[str, Any]) -> set[str]:
    """Return domain keywords for Tier 2 role matching."""
    kws = _model(config).domain_keywords
    return set(kws) if kws else _DEFAULT_DOMAIN_KEYWORDS


def seniority_keywords(config: dict[str, Any]) -> set[str]:
    """Return seniority keywords for Tier 2 role matching."""
    kws = _model(config).seniority_keywords
    return set(kws) if kws else _DEFAULT_SENIORITY_KEYWORDS


def countries_list(config: dict[str, Any]) -> list[str]:
    """Return the configured ISO country codes, defaulting to US + CA."""
    loc = locations_config(config)
    return list(loc.countries) if loc and loc.countries else ["US", "CA"]


def _known_urls_from_config(config: dict[str, Any]) -> set[str]:
    """Return URLs the orchestrator already knows about (jobs.csv + archive).

    Read from the transient ``_known_urls`` key set by ``scraper.run()``.
    Empty set when absent (e.g., direct ``scrape_*`` calls in tests) —
    scrapers treat that as "skip nothing", preserving pre-W6 behavior.
    """
    return config.get("_known_urls", set())


def location_matches(job: dict, config: dict) -> bool:
    """Check whether a job's location matches the configured allow-list.

    Accepts if any of:
      - remote: true and job location contains "remote" (or is empty/missing)
      - job location contains any entry from locations.cities
      - job location contains a country name from locations.countries
    Returns True (accept) when no locations block is configured.
    """
    loc_cfg = locations_config(config)
    if loc_cfg is None:
        return True

    loc = job.get("location", "").lower()
    if not loc:
        # Empty location implies remote-friendly; accept when remote is enabled
        return loc_cfg.remote

    if loc_cfg.remote and "remote" in loc:
        return True

    for city in loc_cfg.cities:
        if city.lower() in loc:
            return True

    for code in loc_cfg.countries:
        for name in COUNTRY_NAMES.get(code.upper(), []):
            if name.lower() in loc:
                return True

    return False


# ── Dedup helpers ─────────────────────────────────────────────────────────────


_WHITESPACE_RE = re.compile(r"\s+")


def _dedup_norm(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", s.lower().strip())


def dedup_key(company: str, role: str) -> str:
    """Normalized dedup key for cross-site duplicate detection.

    Lowercases and collapses whitespace in both fields so the same job posted
    on RemoteOK and LinkedIn produces an identical key.

    For typed callers, prefer ``dedup_key_for(job: NormalizedJob)``.
    """
    return f"{_dedup_norm(company)}::{_dedup_norm(role)}"


def dedup_key_for(job: "NormalizedJob") -> str:  # noqa: F821
    """Typed dedup key (K5): operates directly on NormalizedJob.

    Equivalent to ``dedup_key(job.company, job.role)`` but skips the dict-key
    plumbing in the orchestrator. Both forms must produce identical keys —
    test_dedup_typed_matches_legacy guards that invariant.
    """
    return f"{_dedup_norm(job.company)}::{_dedup_norm(job.role)}"


# Location strings scrapers emit for fully-remote roles. All collapse to "Remote"
# so downstream consumers see one canonical value.
_REMOTE_LOCATION_ALIASES: frozenset[str] = frozenset(
    {
        "",
        "anywhere",
        "worldwide",
        "remote - anywhere",
        "remote, worldwide",
    }
)

# Role-title suffixes appended by some scrapers to signal remote eligibility.
# Stripped here so dedup and display see clean titles.
_REMOTE_ROLE_SUFFIXES: tuple[str, ...] = (" (remote)", "(remote)", " - remote")


def normalize_typed(raw: "RawScrapedJob") -> "NormalizedJob":  # noqa: F821
    """Typed normalizer: ``RawScrapedJob -> NormalizedJob``.

    Thin re-export of ``NormalizedJob.from_raw`` so callers can stay on
    ``daily_driver.scraper`` without crossing into the model layer directly.
    The legacy dict-based ``normalize_job`` below remains for callers that
    pass partial dicts through ``__init__.py``'s orchestrator; it will be
    collapsed into this typed entry point at K9.
    """
    from daily_driver.scraper.models import NormalizedJob

    return NormalizedJob.from_raw(raw)


def normalize_job(raw: dict, source: str) -> dict:
    """Canonicalize a scraped job dict before it is written to CSV.

    Responsibilities:
      - Location: collapses known remote-only aliases to "Remote".
      - Role title: strips trailing remote-eligibility suffixes.
      - Greenhouse source: splits "Greenhouse (board)" into in-memory keys
        source_canonical and source_board for downstream filtering. The
        original source value is preserved so append_jobs writes the same
        CSV column as before.
      - Comp: parses the display string into in-memory structured fields
        (comp_min_native, comp_max_native, comp_currency, comp_period,
        comp_min_usd, comp_max_usd). These fields are NOT written to CSV;
        DictWriter extrasaction="ignore" drops them automatically.

    Returns a new dict with canonical keys added; does not modify raw in place.
    Does not touch GD rating (W2-E) or Wellfound-specific fields (W2-A).

    Example::
        >>> normalize_job(
        ...     {"location": "Anywhere", "role": "Foo (Remote)", "source": "Greenhouse (acme)"},
        ...     "Greenhouse (acme)",
        ... )
        # location -> "Remote", role -> "Foo", source_canonical -> "greenhouse",
        # source_board -> "acme"
    """
    job = dict(raw)

    loc = job.get("location", "").strip()
    if loc.lower() in _REMOTE_LOCATION_ALIASES:
        job["location"] = "Remote"
    else:
        job["location"] = loc

    role = job.get("role", "")
    role_lower = role.lower()
    for suffix in _REMOTE_ROLE_SUFFIXES:
        if role_lower.endswith(suffix):
            role = role[: len(role) - len(suffix)].rstrip()
            role_lower = role.lower()
            break
    job["role"] = role

    # "Greenhouse (board-slug)" -> in-memory canonical fields only.
    # Kept separate from source so CSV output is unchanged.
    src = source or job.get("source", "")
    if src.startswith("Greenhouse (") and src.endswith(")"):
        job["source_canonical"] = "greenhouse"
        job["source_board"] = src[len("Greenhouse (") : -1]
    else:
        job["source_canonical"] = src.split("/")[0].lower() if src else ""
        job["source_board"] = ""

    job.update(_parse_comp(job.get("comp", "") or ""))

    return job


# ── Role matching ─────────────────────────────────────────────────────────────


def _split_roles(roles: list[str]) -> tuple[list[str], list[str]]:
    """Partition roles into (include, exclude). '!'-prefixed entries are
    exclusions; the leading '!' is stripped before pattern compilation."""
    include: list[str] = []
    exclude: list[str] = []
    for r in roles:
        if r.startswith("!"):
            exclude.append(r[1:])
        else:
            include.append(r)
    return include, exclude


def _role_pattern(role: str) -> re.Pattern | None:
    """Compile a wildcarded role to a case-insensitive substring regex.

    Returns None for plain literals so the caller can stay on the fast path.
    Only '*' is treated as a wildcard; all other chars are regex-escaped so
    entries like 'CI/CD Engineer' and 'C++ *' keep working unchanged.
    """
    if "*" not in role:
        return None
    pattern = re.escape(role).replace(r"\*", r".*")
    return re.compile(pattern, re.IGNORECASE)


def _role_matches(role: str, title: str, title_lower: str) -> bool:
    """Uniform check: literal substring or compiled wildcard pattern."""
    pat = _role_pattern(role)
    if pat is None:
        return role.lower() in title_lower
    return pat.search(title) is not None


def matches_roles(title: str, roles: list[str], config: dict | None = None) -> bool:
    """True if the job title is relevant based on configured roles.

    Exclusions (entries starting with '!') short-circuit and dominate over
    every other tier — they reject titles that would otherwise pass Tier 2/2b.
    Tier 1: literal or wildcarded include match.
    Tier 2: domain + seniority keywords both present.
    Tier 2b: standalone SRE / Platform Engineer keyword match.
    """
    include, exclude = _split_roles(roles)
    title_lower = title.lower()

    for role in exclude:
        if _role_matches(role, title, title_lower):
            return False

    for role in include:
        if _role_matches(role, title, title_lower):
            return True

    d_kws = domain_keywords(config) if config else _DEFAULT_DOMAIN_KEYWORDS
    s_kws = seniority_keywords(config) if config else _DEFAULT_SENIORITY_KEYWORDS
    has_domain = any(kw in title_lower for kw in d_kws)
    has_seniority = any(kw in title_lower for kw in s_kws)
    if has_domain and has_seniority:
        return True

    # SRE, Platform Engineer, and the spelled-out "Site Reliability Engineer"
    # match without a seniority prefix — they are precise enough as role names
    # that the senior-only filter is delegated to config exclusions
    # ("!Junior *", "!*Internship*", "!*Manager*", etc.). Broader terms
    # (DevOps, Infrastructure) still require a seniority qualifier via Tier 2.
    if any(
        kw in title_lower
        for kw in {"sre", "platform engineer", "site reliability engineer"}
    ):
        return True

    return False


# ── Search term helpers ───────────────────────────────────────────────────────


def _compress_search_terms(roles: list[str]) -> list[str]:
    """Deduplicate 21 roles → ~10 base types by stripping seniority prefixes.

    Preserves the original casing of the base term as it appears in the roles
    list (e.g. "CI/CD Engineer" stays as-is). Reduces Playwright page loads
    from 21 to ~10 per site.
    """
    seen: set[str] = set()
    result: list[str] = []
    for role in roles:
        if role.startswith("!"):
            continue  # exclusions don't drive URL searches
        if "*" in role:
            log.debug("[search-terms] skipping wildcard role %r", role)
            continue
        lower = role.lower()
        base = role
        for prefix in _SENIORITY_PREFIXES:
            if lower.startswith(prefix):
                base = role[len(prefix) :]
                break
        key = base.lower()
        if key not in seen:
            seen.add(key)
            result.append(base)
    return result


def _search_terms(config: dict) -> list[str]:
    """Return URL search query strings, compressed to base role types.

    Override by setting job_search.scraper.search_terms in config.
    """
    explicit = scraper_cfg(config).search_terms
    if explicit:
        return list(explicit)
    return _compress_search_terms(roles_list(config))


# ── Orchestrator ──────────────────────────────────────────────────────────────

SCRAPERS: dict[str, Callable[[dict], list[dict]]] = {
    "remoteok": scrape_remoteok,
    "weworkremotely": scrape_weworkremotely,
    "hn_who_is_hiring": scrape_hn_who_is_hiring,
    "greenhouse": scrape_greenhouse,
    "jobspy": scrape_jobspy,
    "wellfound": scrape_wellfound,
    "apple": scrape_apple,
}


def _typed_source(
    fn: Callable[[dict[str, Any]], list[dict[str, Any]]],
) -> Callable[[dict[str, Any]], list[Any]]:
    """Wrap a dict-returning scraper into a Source-protocol callable.

    Validates each row through ``RawScrapedJob`` (Q15: ``extra='ignore'``).
    Rows that fail validation (empty role, etc.) are dropped with a debug log
    rather than aborting the whole source.
    """
    from daily_driver.scraper.models import RawScrapedJob

    def wrapped(config: dict[str, Any]) -> list[Any]:
        out: list[Any] = []
        for row in fn(config):
            try:
                out.append(
                    RawScrapedJob.model_validate(
                        {
                            "company": row.get("company", ""),
                            "role": row.get("role", ""),
                            "url": row.get("url", ""),
                            "source": row.get("source", ""),
                            "location": row.get("location", ""),
                            "comp_display": row.get("comp", "") or "",
                            "date_found": row.get("date_found") or today().isoformat(),
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("[%s] dropped invalid row: %s", fn.__name__, exc)
        return out

    wrapped.__name__ = f"typed_{fn.__name__}"
    return wrapped


# Q16: explicit Source registry — no dynamic dispatch, all 7 sources known
# at import time. Each entry is a Source-protocol callable that validates
# rows through RawScrapedJob.
SOURCE_REGISTRY: dict[str, Callable[[dict[str, Any]], list[Any]]] = {
    sid: _typed_source(fn) for sid, fn in SCRAPERS.items()
}


def _non_headless_sources(config: dict) -> frozenset[str]:
    """Derive the set of sources that require a visible browser from config.

    A source entry with type: playwright in job_search.scraper.sources signals
    that it must run in non-headless mode. Plain bool entries default to headless.
    """
    sources = scraper_cfg(config).sources
    return frozenset(
        sid
        for sid, entry in sources.items()
        if isinstance(entry, dict) and entry.get("type") == "playwright"
    )


def _config_with_headless(config: dict, headless: bool) -> dict:
    """Return a deep copy of config with job_search.scraper.headless overridden.

    Scrapers read headless via scraper_cfg(config); passing a phase-specific
    copy lets the orchestrator force headless mode per phase without threading
    a kwarg through every scraper function.
    """
    cfg = copy.deepcopy(config)
    cfg.setdefault("job_search", {}).setdefault("scraper", {})["headless"] = headless
    return cfg


def _run_one(source_id: str, cfg: dict) -> list[dict] | Exception:
    """Invoke one scraper and map known failures to exceptions.

    Returns the job list on success, or the caught exception on failure. The
    orchestrator classifies exceptions into failed_sources during merge.
    """
    scraper_fn = SCRAPERS[source_id]
    timeout = timeout_seconds(cfg)
    start = time.perf_counter()
    try:
        jobs = scraper_fn(cfg)
    except requests.exceptions.Timeout as exc:
        log.warning("[%s] timed out after %ds", source_id, timeout)
        return exc
    except requests.exceptions.RequestException as exc:
        log.warning("[%s] request failed: %s", source_id, exc)
        return exc
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] unexpected error: %s", source_id, exc, exc_info=True)
        return exc
    elapsed = time.perf_counter() - start
    log.info("[%s] took %.1fs (%d jobs)", source_id, elapsed, len(jobs))
    return jobs


def _merge_and_dedup(
    results: list[tuple[str, list[dict] | Exception]],
    config: dict,
) -> tuple[list[dict], list[str]]:
    """Merge per-source results, deduplicating by URL and company+role key.

    First-scraper-wins: iteration order of `results` determines which job wins
    a dedup collision. Exceptions are collected into failed_sources.
    """
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    failed_sources: list[str] = []

    for source_id, result in results:
        if isinstance(result, Exception):
            failed_sources.append(source_id)
            continue
        for job in result:
            url = job.get("url", "")
            key = dedup_key(job.get("company", ""), job.get("role", ""))
            if (url and url in seen_urls) or (key and key in seen_keys):
                continue
            if url:
                seen_urls.add(url)
            if key:
                seen_keys.add(key)
            all_jobs.append(job)

    return all_jobs, failed_sources


def run_all_scrapers(
    config: dict,
    *,
    sources_override: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Run all enabled scrapers and deduplicate results within this run.

    Two phases:
      Phase 1 — headless-safe sources run in parallel via ThreadPoolExecutor
      Phase 2 — non-headless sources (wellfound, apple) run serially

    Phase 2 stays serial by design: running multiple visible Chromium windows
    concurrently is RAM-heavy and makes bot detection easier. Deduplicates by
    both URL and company+role key so the same job appearing on multiple boards
    is only kept once (first scraper wins).

    When ``sources_override`` is provided, only those source IDs run regardless
    of the ``scraper.sources`` toggles in config. Caller is responsible for
    validating IDs against ``SCRAPERS``.
    """
    cfg = scraper_cfg(config)
    source_cfg = cfg.sources
    workers = cfg.parallel_workers

    if sources_override is not None:
        override = set(sources_override)
        enabled = [sid for sid in SCRAPERS if sid in override]
        disabled = [sid for sid in SCRAPERS if sid not in override]
        log.info("[--sources] running only: %s", ", ".join(enabled) or "(none)")
    else:

        def _is_enabled(sid: str) -> bool:
            toggle = source_cfg.get(sid)
            return toggle.enabled if toggle is not None else False

        enabled = [sid for sid in SCRAPERS if _is_enabled(sid)]
        disabled = [sid for sid in SCRAPERS if not _is_enabled(sid)]
    for sid in disabled:
        log.info("[%s] disabled in config, skipping", sid)

    non_headless = _non_headless_sources(config)
    headless_sources = [sid for sid in enabled if sid not in non_headless]
    visible_sources = [sid for sid in enabled if sid in non_headless]

    results: list[tuple[str, list[dict] | Exception]] = []

    # Phase 1: headless, parallel
    if headless_sources:
        headless_cfg = _config_with_headless(config, True)
        log.info(
            "[phase1] running %d headless scrapers, %d workers",
            len(headless_sources),
            workers,
        )
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(_run_one, sid, headless_cfg): sid
                for sid in headless_sources
            }
            for fut in as_completed(futures):
                sid = futures[fut]
                results.append((sid, fut.result()))

    # Phase 2: non-headless, serial (preserves pre-parallel behavior)
    if visible_sources:
        visible_cfg = _config_with_headless(config, False)
        log.info(
            "[phase2] running %d non-headless scrapers serially",
            len(visible_sources),
        )
        for sid in visible_sources:
            results.append((sid, _run_one(sid, visible_cfg)))

    return _merge_and_dedup(results, config)


# ── Notification ─────────────────────────────────────────────────────────────


def _notify_new_jobs(count: int, csv_path: Path) -> None:
    title = "Job Scraper"
    message = f"{count} new jobs found"
    file_url = csv_path.as_uri()

    if shutil.which("terminal-notifier"):
        subprocess.run(
            [
                "terminal-notifier",
                "-title",
                title,
                "-message",
                message,
                "-open",
                file_url,
            ],
            check=False,
        )
    else:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{message}" with title "{title}" subtitle "{csv_path.name}"',
            ],
            check=False,
        )


# ── Entry point ───────────────────────────────────────────────────────────────
