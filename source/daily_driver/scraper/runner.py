"""Scraper orchestration: config helpers, role/dedup logic, run() / run_backfill()."""

from __future__ import annotations

import copy
import csv
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
import yaml

from daily_driver.core.config_models import JobSearchPlugin, Locations, ScraperConfig
from daily_driver.core.console import Console
from daily_driver.core.jobs_lock import jobs_lock_path
from daily_driver.core.locking import file_lock
from daily_driver.integrations.notify import desktop_notify
from daily_driver.scraper.comp import _parse_comp
from daily_driver.scraper.sources import SCRAPERS
from daily_driver.scraper.sources._http import COUNTRY_NAMES

if TYPE_CHECKING:
    from daily_driver.scraper.models import NormalizedJob, RawScrapedJob

log = logging.getLogger(__name__)


class ScraperError(RuntimeError):
    """Raised on unrecoverable scraper errors.

    Library code raises this instead of calling sys.exit() so the CLI layer
    can decide how to report and exit.
    """


def _to_int(x: object) -> int | None:
    """Best-effort coerce an arbitrary value to int (via float). None on failure."""
    try:
        return int(float(x))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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


def max_retries(config: dict[str, Any]) -> int:
    """Number of additional attempts on rate-limited (429/503) HTTP responses."""
    return _model(config).scraper.max_retries


def max_age_days(config: dict[str, Any]) -> int:
    """Maximum age (in days) for scraped postings. 0 disables the filter."""
    return _model(config).scraper.max_age_days


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


def dedup_key_for(job: NormalizedJob) -> str:  # noqa: F821
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


def normalize_typed(raw: RawScrapedJob) -> NormalizedJob:  # noqa: F821
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


# Sources that require a full (non-headless) browser. SourceToggle has
# extra="forbid" so a config-based `type: playwright` key is rejected by
# pydantic -- browser classification must live in code, not config.
_PLAYWRIGHT_SOURCES: frozenset[str] = frozenset({"apple"})


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

    Emits an unconditional user-facing start/finish pair on stdout (bypassing
    quiet-mode and verbosity gates) so `daily-driver jobs run` shows progress
    before any scraper completes. The matching log lines stay on stderr for
    the debug stream.
    """
    scraper_fn = SCRAPERS[source_id]
    timeout = timeout_seconds(cfg)
    user_console = Console.get_user_console()
    user_console.print(f"Now checking {source_id}...")
    log.info("[%s] starting", source_id)
    start = time.perf_counter()
    try:
        jobs = scraper_fn(cfg)
    except requests.exceptions.Timeout as exc:
        log.warning("[%s] timed out after %ds", source_id, timeout)
        user_console.print(f"  {source_id}: failed (timed out after {timeout}s)")
        return exc
    except requests.exceptions.RequestException as exc:
        log.warning("[%s] request failed: %s", source_id, exc)
        user_console.print(f"  {source_id}: failed ({exc})")
        return exc
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] unexpected error: %s", source_id, exc, exc_info=True)
        user_console.print(f"  {source_id}: failed ({exc})")
        return exc
    elapsed = time.perf_counter() - start
    log.info("[%s] took %.1fs (%d jobs)", source_id, elapsed, len(jobs))
    user_console.print(f"  {source_id}: {len(jobs)} jobs ({elapsed:.1f}s)")
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
      Phase 2 — non-headless sources (apple) run serially

    Phase 2 stays serial by design: running multiple visible Firefox windows
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
            if sid.startswith("jobspy_"):
                site = sid[len("jobspy_") :]
                toggle = source_cfg.get("jobspy")
                if toggle is None or not toggle.enabled:
                    return False
                return getattr(toggle, site, True)
            toggle = source_cfg.get(sid)
            return toggle.enabled if toggle is not None else False

        enabled = [sid for sid in SCRAPERS if _is_enabled(sid)]
        disabled = [sid for sid in SCRAPERS if not _is_enabled(sid)]
    for sid in disabled:
        log.info("[%s] disabled in config, skipping", sid)

    non_headless = _PLAYWRIGHT_SOURCES
    headless_sources = [sid for sid in enabled if sid not in non_headless]
    visible_sources = [sid for sid in enabled if sid in non_headless]

    results: list[tuple[str, list[dict] | Exception]] = []

    # Phase 1: headless, parallel
    if headless_sources:
        headless_cfg = _config_with_headless(config, True)
        log.info(
            "[phase1] running %d headless scrapers (%s), %d workers",
            len(headless_sources),
            ", ".join(headless_sources),
            workers,
        )
        pool = ThreadPoolExecutor(max_workers=max(1, workers))
        try:
            futures = {
                pool.submit(_run_one, sid, headless_cfg): sid
                for sid in headless_sources
            }
            for fut in as_completed(futures):
                sid = futures[fut]
                results.append((sid, fut.result()))
        except KeyboardInterrupt:
            # Drop pending (unstarted) futures and stop waiting on the pool.
            # In-flight HTTP requests cannot be killed mid-call; they run to
            # their per-source ``timeout`` before their threads exit. The CLI
            # boundary catches this re-raise and prints a clean message.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

    # Phase 2: non-headless, serial (preserves pre-parallel behavior)
    if visible_sources:
        visible_cfg = _config_with_headless(config, False)
        log.info(
            "[phase2] running %d non-headless scrapers serially (%s)",
            len(visible_sources),
            ", ".join(visible_sources),
        )
        for sid in visible_sources:
            results.append((sid, _run_one(sid, visible_cfg)))

    return _merge_and_dedup(results, config)


# ── Notification ─────────────────────────────────────────────────────────────


def _notify_new_jobs(count: int, csv_path: Path) -> None:
    desktop_notify(f"{count} new jobs found", "Job Scraper", csv_path)


# ── Public entry points ──────────────────────────────────────────────────────


def load_config_file(config_path: Path) -> dict[str, Any]:
    """Load a YAML config file into the raw-dict shape the scraper expects.

    Raises ValueError when the caller points at a legacy ``config.yaml``.
    All scraper settings now
    belong under ``plugins.job_search`` in ``.dd-config.yaml``.
    See docs/configuration.md for the current schema.
    """
    if config_path.name == "config.yaml":
        raise ValueError(
            f"{config_path} is a legacy config file. "
            "All scraper settings have moved to plugins.job_search in .dd-config.yaml. "
            "See docs/configuration.md for the current schema."
        )
    return load_config(config_path)


def run_backfill(config: dict[str, Any], csv_path: Path) -> None:
    """Re-enrich empty fields in an existing jobs.csv."""
    from daily_driver.scraper.csv_io import backfill

    backfill(config, csv_path)


def _print_dry_run_table(jobs: list[dict[str, Any]]) -> None:
    """Render a Rich table summary of dry-run matches."""
    from rich.console import Console
    from rich.table import Table

    from daily_driver.scraper.parsing import _fix_mojibake

    console = Console(stderr=False)
    if not jobs:
        console.print("[dim]Dry-run: no new jobs matched.[/dim]")
        return

    table = Table(
        title=f"Dry-run preview ({len(jobs)} new jobs)",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Source")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Location")
    table.add_column("URL", overflow="fold")
    for j in jobs:
        table.add_row(
            _fix_mojibake(str(j.get("source", ""))),
            _fix_mojibake(str(j.get("company", ""))),
            _fix_mojibake(str(j.get("role", ""))),
            _fix_mojibake(str(j.get("location", ""))),
            str(j.get("url", "")),
        )
    console.print(table)
    console.print(
        f"[dim]{len(jobs)} new jobs (dry-run, nothing written).[/dim]",
    )


def run(
    config: dict[str, Any],
    output_dir: Path,
    *,
    dry_run: bool = False,
    sources_override: list[str] | None = None,
) -> int:
    """Run all enabled scrapers and append new rows to ``output_dir/jobs.csv``.

    Returns the process-style exit code (0 success, 1 on failed sources /
    I/O error). Mirrors the behavior of the legacy ``scripts/scrape-jobs.py``
    ``main()`` without performing argparse or ``sys.exit()`` — the CLI layer
    is responsible for exit handling.
    """
    from daily_driver.scraper.comp import comp_meets_threshold, currency_matches_primary
    from daily_driver.scraper.csv_io import (
        CANONICAL_HEADER,
        _migrate_legacy_header,
        append_jobs,
        load_existing_jobs,
    )
    from daily_driver.scraper.enrichment import (
        enrich_company_descriptions,
        enrich_fit_and_notes,
        enrich_job_details,
    )

    started_at = datetime.now(timezone.utc)
    csv_path = output_dir / "jobs.csv"
    lock_path = jobs_lock_path(csv_path)

    validate_config(config)
    Console.info("Starting jobs run...")

    if not scraper_cfg(config).enabled:
        Console.warning(
            "Scraper disabled. Set plugins.job_search.scraper.enabled: true "
            "in .dd-config.yaml"
        )
        return 0

    from daily_driver.core.jobs_archive import load_archive_dedup

    with file_lock(lock_path):
        known_urls, known_keys, header = load_existing_jobs(csv_path)

        # Union archive-table dedup state so triaged listings (pruned to
        # jobs.archive.csv) are never re-discovered.
        archive_urls, archive_keys = load_archive_dedup(csv_path)
        known_urls |= archive_urls
        known_keys |= archive_keys

        if not header:
            header = CANONICAL_HEADER
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(header)
            except OSError as exc:
                log.error("Cannot initialize %s: %s", csv_path, exc)
                return 1
        else:
            header = _migrate_legacy_header(csv_path, header)

    log.info(
        "Loaded %d existing URLs, %d existing keys from %s",
        len(known_urls),
        len(known_keys),
        csv_path,
    )
    Console.info(
        f"Loaded existing job index ({len(known_urls)} URLs, {len(known_keys)} keys)."
    )

    # Stuff the merged dedup set into the config dict so adapters that build
    # their URL deterministically (Apple, Wellfound) can short-circuit during
    # pagination instead of waiting for the post-scrape filter below.
    config["_known_urls"] = known_urls

    if sources_override:
        Console.info(f"Scraping selected sources: {', '.join(sources_override)}")
    else:
        Console.info("Scraping enabled sources from config...")
    all_jobs, failed_sources = run_all_scrapers(
        config, sources_override=sources_override
    )

    new_jobs = [
        j
        for j in all_jobs
        if (not j.get("url") or j["url"] not in known_urls)
        and dedup_key(j.get("company", ""), j.get("role", "")) not in known_keys
    ]

    urlless = [j for j in new_jobs if not j.get("url")]
    if urlless:
        log.warning(
            "Dropping %d jobs with no URL (cannot dedup on future runs)",
            len(urlless),
        )
    new_jobs = [j for j in new_jobs if j.get("url")]

    Console.info(f"Scrape complete: {len(all_jobs)} total jobs, {len(new_jobs)} new.")
    log.info("Found %d jobs total, %d new", len(all_jobs), len(new_jobs))

    pre_filter = len(new_jobs)
    new_jobs = [j for j in new_jobs if location_matches(j, config)]
    filtered = pre_filter - len(new_jobs)
    if filtered:
        Console.info(f"Filtered {filtered} jobs by location preferences.")
        log.info("Filtered %d jobs by location preferences", filtered)

    if failed_sources:
        log.warning("Failed sources: %s", ", ".join(failed_sources))

    if not dry_run:
        Console.info("Enriching job details...")
        enrich_job_details(new_jobs, config)
    else:
        Console.info("Dry-run mode: skipping job-detail enrichment.")
        log.info("[dry-run] skipping enrich_job_details (claude calls)")
    new_jobs = [normalize_job(j, j.get("source", "")) for j in new_jobs]

    skipped_below_comp = 0
    for job in new_jobs:
        if job.get("status") == "skipped":
            continue
        ok, reason = comp_meets_threshold(job, config)
        if ok:
            continue
        job["status"] = "skipped"
        existing_notes = (job.get("notes") or "").strip()
        job["notes"] = f"{existing_notes}; {reason}" if existing_notes else reason
        skipped_below_comp += 1
        log.info(
            "[comp-filter] skipped: %s | %s | comp=%r",
            job.get("company", "?"),
            job.get("role", "?"),
            job.get("comp", ""),
        )
    log.info(
        "Comp-threshold filter: %d skipped below $%d USD",
        skipped_below_comp,
        min_comp_usd(config),
    )

    pre_currency = len(new_jobs)
    new_jobs = [j for j in new_jobs if currency_matches_primary(j, config)]
    skipped_currency = pre_currency - len(new_jobs)
    if skipped_currency:
        log.info(
            "Primary-currency filter: %d dropped (not matching %s)",
            skipped_currency,
            _model(config).primary_currency,
        )

    if dry_run:
        Console.info("Dry-run mode: skipping product + fit/notes enrichment.")
        log.info(
            "[dry-run] skipping enrich_company_descriptions and enrich_fit_and_notes (claude calls)"
        )
        product_stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
        fn_stats = {
            "enriched": 0,
            "skipped_budget": 0,
            "skipped_no_desc": 0,
            "failed": 0,
        }
    else:
        product_stats = enrich_company_descriptions(new_jobs, config)
        fn_stats = enrich_fit_and_notes(new_jobs, config)

    n = len(new_jobs)
    log.info(
        "Fit+Notes enriched: %d/%d, %d skipped (budget), %d failed (parse/subprocess)",
        fn_stats["enriched"],
        n,
        fn_stats["skipped_budget"],
        fn_stats["failed"],
    )
    log.info(
        "Product enriched: %d/%d, %d skipped (cached), %d failed",
        product_stats["enriched"],
        n,
        product_stats["skipped_cached"],
        product_stats["failed"],
    )
    Console.info(
        "Enrichment complete: "
        f"fit+notes {fn_stats['enriched']}/{n}, product {product_stats['enriched']}/{n}."
    )

    if dry_run:
        Console.success(
            f"Dry-run complete: {len(new_jobs)} new jobs ready (nothing written)."
        )
        _print_dry_run_table(new_jobs)
        return 1 if failed_sources else 0

    # Re-acquire the sentinel only for the append. The lock was dropped during
    # enrichment above so slow LLM calls don't block concurrent prune/backfill.
    with file_lock(lock_path):
        written = append_jobs(csv_path, new_jobs, header)
    Console.success(
        f"Scraper complete: {written} new jobs appended to {csv_path} "
        f"({skipped_below_comp} skipped below comp threshold)."
    )

    run_manifest = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "sources_ok": sorted(
            {
                j.get("source", "")
                for j in all_jobs
                if j.get("source") and j.get("source") not in failed_sources
            }
        ),
        "sources_failed": failed_sources,
        "new_jobs": written,
        "enriched_fit_notes": fn_stats["enriched"],
        "enriched_product": product_stats["enriched"],
        "skipped_below_comp": skipped_below_comp,
    }
    last_run_path = output_dir / "jobs-last-run.json"
    try:
        last_run_path.write_text(
            json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8"
        )
        log.info("Run manifest written to %s", last_run_path)
    except OSError as exc:
        log.warning("Could not write run manifest: %s", exc)

    if failed_sources:
        log.error("Scraper failures: %s", ", ".join(failed_sources))
        return 1

    if written > 0:
        _notify_new_jobs(written, csv_path)
    return 0


__all__ = [
    "ScraperError",
    "_to_int",
    "_DEFAULT_DOMAIN_KEYWORDS",
    "_DEFAULT_SENIORITY_KEYWORDS",
    "_SENIORITY_PREFIXES",
    "load_config",
    "_model",
    "validate_config",
    "min_comp_usd",
    "scraper_cfg",
    "roles_list",
    "user_agent",
    "timeout_seconds",
    "max_retries",
    "max_age_days",
    "enrich_timeout",
    "locations_config",
    "persona",
    "home_city",
    "domain_keywords",
    "seniority_keywords",
    "countries_list",
    "_known_urls_from_config",
    "location_matches",
    "_WHITESPACE_RE",
    "_dedup_norm",
    "dedup_key",
    "dedup_key_for",
    "_REMOTE_LOCATION_ALIASES",
    "_REMOTE_ROLE_SUFFIXES",
    "normalize_typed",
    "normalize_job",
    "_split_roles",
    "_role_pattern",
    "_role_matches",
    "matches_roles",
    "_compress_search_terms",
    "_search_terms",
    "_PLAYWRIGHT_SOURCES",
    "_config_with_headless",
    "_run_one",
    "_merge_and_dedup",
    "run_all_scrapers",
    "_notify_new_jobs",
    "load_config_file",
    "run_backfill",
    "_print_dry_run_table",
    "run",
]
