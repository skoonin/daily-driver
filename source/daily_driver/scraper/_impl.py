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
import urllib.parse
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from daily_driver.scraper.models import (
        NormalizedJob,
        RawScrapedJob,
    )

if sys.platform != "win32":
    pass

import requests
import yaml
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

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


# ── Scrapers ──────────────────────────────────────────────────────────────────


def scrape_remoteok(config: dict) -> list[dict]:
    """Fetch jobs from RemoteOK's public JSON API.

    GET https://remoteok.com/api returns all current listings as JSON.
    No auth or browser required. We filter client-side with matches_roles().
    """
    roles = roles_list(config)
    session = _http_session(config)
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    resp = _api_get(session, "https://remoteok.com/api", config, label="remoteok")
    if not resp:
        return jobs

    for item in resp.json():
        if "position" not in item:
            continue
        role = item["position"]
        if not matches_roles(role, roles, config):
            continue
        job_id = str(item.get("id", ""))
        if job_id in seen_ids:
            continue
        if job_id:
            seen_ids.add(job_id)
        sal_min = item.get("salary_min")
        sal_max = item.get("salary_max")
        currency = item.get("salary_currency") or "USD"
        prefix = "$" if currency == "USD" else f"{currency} "
        comp = (
            f"{prefix}{int(sal_min):,}-{prefix}{int(sal_max):,}/yr"
            if sal_min and sal_max
            else ""
        )
        job: dict = {
            "company": item.get("company", ""),
            "role": role,
            "location": item.get("location", "") or "Remote",
            "url": item.get("url", ""),
            "source": "RemoteOK",
            "date_found": today().isoformat(),
        }
        if comp:
            job["comp"] = comp
        jobs.append(job)

    log.info("[remoteok] %d jobs matched", len(jobs))
    return jobs


def scrape_weworkremotely(config: dict) -> list[dict]:
    """Fetch jobs from WeWorkRemotely's public RSS feeds.

    WWR publishes per-category RSS at
    /categories/remote-{category}-jobs.rss. Categories are configured under
    plugins.job_search.scraper.wwr_categories in .dd-config.yaml.
    No auth or browser required.
    """
    roles = roles_list(config)
    session = _http_session(config)
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    categories = scraper_cfg(config).wwr_categories
    if not categories:
        log.warning("[weworkremotely] no wwr_categories configured; skipping")
        return jobs

    for category in categories:
        url = f"https://weworkremotely.com/categories/remote-{category}-jobs.rss"
        resp = _api_get(session, url, config, label="weworkremotely")
        if not resp:
            continue

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            log.warning("[weworkremotely] RSS parse failed for %s: %s", category, exc)
            continue

        for item in root.findall(".//item"):
            raw_title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            region = (item.findtext("region") or "Remote").strip()

            # Title format: "Company: Role Title"
            if ": " in raw_title:
                company, role = raw_title.split(": ", 1)
            else:
                company, role = "", raw_title

            if not role or not matches_roles(role, roles, config):
                continue
            if link in seen_urls:
                continue
            if link:
                seen_urls.add(link)

            jobs.append(
                {
                    "company": company,
                    "role": role,
                    "location": region,
                    "url": link,
                    "source": "We Work Remotely",
                    "date_found": today().isoformat(),
                }
            )

    log.info("[weworkremotely] %d jobs matched", len(jobs))
    return jobs


def scrape_hn_who_is_hiring(config: dict) -> list[dict]:
    """Scrape the current month's HN Who's Hiring thread via Algolia API.

    Index-page fetch resolves the thread ID from HN's HTML (unchanged).
    Comment retrieval uses Algolia's public search API instead of parsing
    HN's HTML comment tree, which breaks on markup changes.
    Comment headline format (convention): "Company | Role | Location | ..."
    """
    max_posts = scraper_cfg(config).hn_max_posts
    roles = roles_list(config)
    session = _http_session(config)
    current_month_str = today().strftime("%B %Y")

    index_resp = _api_get(
        session,
        "https://news.ycombinator.com/submitted?id=whoishiring",
        config,
        label="hn_who_is_hiring",
    )
    if not index_resp:
        return []

    index_soup = BeautifulSoup(index_resp.text, "html.parser")

    thread_id = None
    for a_tag in index_soup.find_all("a"):
        text = a_tag.get_text(strip=True)
        if "Who is hiring?" in text and current_month_str in text:
            href_raw = a_tag.get("href", "")
            href = href_raw if isinstance(href_raw, str) else ""
            if href.startswith("item?id="):
                thread_id = href.split("=", 1)[1]
            elif "item?id=" in href:
                thread_id = href.split("item?id=", 1)[1].split("&")[0]
            break

    if not thread_id:
        log.warning(
            "[hn_who_is_hiring] could not find thread for %s", current_month_str
        )
        return []

    algolia_url = (
        f"https://hn.algolia.com/api/v1/search"
        f"?tags=comment,story_{thread_id}&hitsPerPage=1000"
    )
    algolia_resp = _api_get(
        session, algolia_url, config, label="hn_who_is_hiring_algolia"
    )
    if not algolia_resp:
        return []

    data = algolia_resp.json()
    hits = data.get("hits", [])
    total = data.get("nbHits", len(hits))
    if isinstance(total, int) and total > 1000:
        log.warning(
            "[hn_who_is_hiring] thread has %d comments; only first 1000 retrieved",
            total,
        )

    # Strip HTML tags from Algolia comment_text (simple tag-only removal;
    # avoids pulling in a new dependency for this narrow use case).
    _strip_tags = re.compile(r"<[^>]+>")

    jobs: list[dict[str, Any]] = []
    thread_url = f"https://news.ycombinator.com/item?id={thread_id}"

    for hit in hits:
        if len(jobs) >= max_posts:
            break

        raw_html = hit.get("comment_text") or ""
        if not raw_html:
            continue

        raw_text = _strip_tags.sub("", raw_html).strip()
        # Decode the handful of HTML entities that appear in HN posts.
        raw_text = (
            raw_text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#x27;", "'")
        )
        first_line = raw_text.split("\n")[0].strip()
        if not first_line:
            continue

        parts = [p.strip() for p in first_line.split("|")]
        if len(parts) < 2:
            continue

        company = parts[0]
        role = parts[1] if len(parts) > 1 else ""

        if not matches_roles(" ".join(parts), roles, config):
            continue

        location = "Remote"
        for part in parts[2:]:
            if re.search(r"\bremote\b", part, re.IGNORECASE):
                location = "Remote"
                break
            if len(part) < 40 and "http" not in part and "$" not in part:
                location = part
                break

        comment_id = hit.get("objectID", "")
        url = (
            f"https://news.ycombinator.com/item?id={comment_id}"
            if comment_id
            else thread_url
        )

        jobs.append(
            {
                "company": company,
                "role": role,
                "location": location,
                "url": url,
                "source": "HN Who's Hiring",
                "date_found": today().isoformat(),
            }
        )

    log.info("[hn_who_is_hiring] %d jobs matched", len(jobs))
    return jobs


def scrape_greenhouse(config: dict) -> list[dict]:
    """Scrape jobs from the Greenhouse Job Board API (public, no auth required).

    Reads board slugs from config at job_search.scraper.greenhouse_boards
    (default: ["anthropic"]). Each slug maps to
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    which returns all jobs with full HTML descriptions in a single request.
    """
    roles = roles_list(config)
    boards = scraper_cfg(config).greenhouse_boards
    session = _http_session(config)
    jobs: list[dict] = []

    for board in boards:
        api_url = (
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        )
        resp = _api_get(session, api_url, config, label=f"greenhouse/{board}")
        if not resp:
            continue
        data = resp.json()

        board_jobs = data.get("jobs", [])
        company_name = board.replace("-", " ").title()
        # Use the first job's metadata to get the real company name if available.
        if board_jobs:
            first_meta = board_jobs[0].get("company_name")
            if first_meta:
                company_name = first_meta

        for entry in board_jobs:
            title = entry.get("title", "")
            if not title or not matches_roles(title, roles, config):
                continue

            loc = entry.get("location", {})
            location = loc.get("name", "") if isinstance(loc, dict) else str(loc)

            absolute_url = entry.get("absolute_url", "")
            desc_html = entry.get("content", "")
            desc_text = ""
            if desc_html:
                desc_text = BeautifulSoup(desc_html, "html.parser").get_text(
                    " ", strip=True
                )

            jobs.append(
                {
                    "company": company_name,
                    "role": title,
                    "location": location or "Remote",
                    "url": absolute_url,
                    "source": f"Greenhouse ({board})",
                    "date_found": today().isoformat(),
                    "description_text": desc_text,
                }
            )

        log.info(
            "[greenhouse] %s: %d jobs matched out of %d listed",
            board,
            sum(1 for j in jobs if j["source"] == f"Greenhouse ({board})"),
            len(board_jobs),
        )

    return jobs


# ── JobSpy (LinkedIn + Indeed + Glassdoor + Google) ──────────────────────────

# Country codes JobSpy's indeed engine accepts. Maps from ISO codes used in
# config to the string values JobSpy's country_indeed parameter expects.
_JOBSPY_COUNTRY_MAP: dict[str, str] = {
    "US": "USA",
    "CA": "Canada",
    "GB": "UK",
    "IE": "Ireland",
    "AU": "Australia",
    "ZA": "South Africa",
}

# JobSpy interval values → display suffixes
_JOBSPY_INTERVAL_SUFFIX: dict[str, str] = {
    "yearly": "/yr",
    "monthly": "/mo",
    "weekly": "/wk",
    "daily": "/day",
    "hourly": "/hr",
}


def _jobspy_str(x: object, default: str = "") -> str:
    # JobSpy's DataFrame emits NaN (float) for missing cells; NaN is truthy
    # and breaks `.strip()`, so guard on isinstance(str) before coercion.
    return x.strip() if isinstance(x, str) and x.strip() else default


def _format_jobspy_comp(row: dict) -> str:
    """Build a display string from JobSpy's structured comp fields.

    JobSpy provides min_amount, max_amount, currency, and interval. Returns ""
    when no amount data is present so the detail-page enricher can still
    attempt to fill comp from JSON-LD.
    """
    lo = row.get("min_amount")
    hi = row.get("max_amount")
    currency = _jobspy_str(row.get("currency")).upper()
    interval = _jobspy_str(row.get("interval")).lower()

    lo_i, hi_i = _to_int(lo), _to_int(hi)
    if lo_i is None and hi_i is None:
        return ""

    prefix = _COMP_CURRENCY_PREFIX.get(currency, f"{currency} " if currency else "")
    suffix = _JOBSPY_INTERVAL_SUFFIX.get(interval, "")

    if lo_i is not None and hi_i is not None and lo_i != hi_i:
        amount = f"{lo_i:,}\u2013{hi_i:,}"
    elif hi_i is not None:
        amount = f"{hi_i:,}"
    else:
        amount = f"{lo_i:,}"

    return f"{prefix}{amount}{suffix}"


def jobspy_row_to_raw(row: dict[str, Any]) -> "RawScrapedJob | None":  # noqa: F821
    """Validate a JobSpy DataFrame row through RawScrapedJob (Q15: extra='ignore').

    Returns None when the role is empty (jobspy yields these for ads / non-job
    cards); pydantic would otherwise reject NonEmptyStr role.
    """
    # Local import: models.py uses deferred imports from this module
    # (`_parse_comp`, `_REMOTE_*`); top-level import would cycle until K4/K9.
    from daily_driver.scraper.models import RawScrapedJob

    role = _jobspy_str(row.get("title"))
    if not role:
        return None
    return RawScrapedJob.model_validate(
        {
            "company": _jobspy_str(row.get("company")),
            "role": role,
            "location": _jobspy_str(row.get("location")),
            "url": _jobspy_str(row.get("job_url")),
            # JobSpy "site" is the upstream source name (linkedin, indeed, ...).
            "source": _jobspy_str(row.get("site"), "jobspy"),
            "comp_display": _format_jobspy_comp(row),
            "date_found": today(),
        }
    )


def normalize_jobspy_row(row: dict[str, Any]) -> dict[str, Any]:
    """Adapt a single JobSpy DataFrame record to the scraper dict shape.

    Validates through RawScrapedJob at the boundary (Q15) and dumps back to
    the legacy dict shape for the dict-based pipeline. Carries `description`
    separately because RawScrapedJob does not model enrichment fields.
    """
    raw = jobspy_row_to_raw(row)
    description = _jobspy_str(row.get("description"))
    if raw is None:
        return {
            "company": _jobspy_str(row.get("company")),
            "role": "",
            "location": _jobspy_str(row.get("location")),
            "url": _jobspy_str(row.get("job_url")),
            "source": _jobspy_str(row.get("site"), "jobspy"),
            "description": description,
            "comp": _format_jobspy_comp(row),
            "date_found": today().isoformat(),
        }
    return {
        "company": raw.company,
        "role": raw.role,
        "location": raw.location,
        "url": raw.url,
        "source": raw.source,
        "description": description,
        "comp": raw.comp_display,
        "date_found": raw.date_found.isoformat(),
    }


def scrape_jobspy(config: dict) -> list[dict]:
    """Headless multi-source scraper via JobSpy (LinkedIn, Indeed, Glassdoor, Google).

    Imported lazily so the module still loads when python-jobspy is not yet
    installed — only this scraper fails in that case, not the whole pipeline.
    JobSpy handles its own HTTP session and returns a pandas DataFrame.
    """
    # Lazy import: keeps --help and other scrapers functional without the package.
    try:
        from jobspy import scrape_jobs as jobspy_scrape
    except ImportError:
        log.warning(
            "[jobspy] python-jobspy not installed — run: pip install python-jobspy"
        )
        return []

    cfg = scraper_cfg(config)
    jobspy_cfg = cfg.jobs
    results_wanted = jobspy_cfg.results_wanted_per_query
    hours_old = jobspy_cfg.hours_old
    default_country_indeed = jobspy_cfg.country_indeed

    roles = roles_list(config)
    terms = _search_terms(config)
    countries = countries_list(config)
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    for term in terms:
        for country in countries:
            country_code = country.upper()
            country_indeed = _JOBSPY_COUNTRY_MAP.get(
                country_code, default_country_indeed
            )
            location_name = COUNTRY_NAMES.get(country_code, [country])[0]
            # Glassdoor disabled: JobSpy's Glassdoor path returns HTTP 400
            # ("location not parsed") on every request and retries for minutes.
            sites = ["linkedin", "indeed", "google"]
            try:
                df = jobspy_scrape(
                    site_name=sites,
                    search_term=term,
                    location=location_name,
                    results_wanted=results_wanted,
                    country_indeed=country_indeed,
                    hours_old=hours_old,
                    linkedin_fetch_description=True,
                )
            except TypeError as exc:
                # python-jobspy < 1.1.82 doesn't accept linkedin_fetch_description.
                # Bail out of the entire run with an actionable hint rather than
                # emitting a generic warning per (term, country) iteration.
                log.error(
                    "[jobspy] python-jobspy is too old (%s); upgrade with "
                    "`pip install -U 'python-jobspy>=1.1.82'` or `make setup`",
                    exc,
                )
                return jobs
            except Exception as exc:
                log.warning(
                    "[jobspy] scrape failed for %r / %s: %s", term, country, exc
                )
                continue

            if df is None or df.empty:
                log.debug("[jobspy] no results for %r / %s", term, country)
                continue

            records = df.to_dict("records")
            for row in records:
                normalized = normalize_jobspy_row(row)
                if not normalized["role"] or not matches_roles(
                    normalized["role"], roles, config
                ):
                    continue
                url = normalized["url"]
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                # Preserve description from JobSpy so detail-page enrichment
                # is skipped (enrich_job_details already short-circuits on comp,
                # and the enricher skips rows that already have description_text).
                normalized["description_text"] = normalized.pop("description", "")
                jobs.append(normalized)

            log.debug(
                "[jobspy] %s / %s: %d records, %d matched after role filter",
                term,
                country,
                len(records),
                sum(1 for j in jobs if j.get("date_found") == today().isoformat()),
            )

    log.info("[jobspy] %d jobs matched across all terms and countries", len(jobs))
    return jobs


def scrape_wellfound(config: dict) -> list[dict]:
    """Playwright scraper for Wellfound (formerly AngelList Talent) job search.

    Public search results visible without login are limited. Uses domcontentloaded
    plus a fixed wait for React hydration — networkidle never resolves on this SPA.
    CSS class names are hashed so multiple selectors are tried.
    """
    roles = roles_list(config)
    terms = _search_terms(config)
    timeout_ms = timeout_seconds(config) * 1000
    from daily_driver.core.config_models import PlaywrightDelays

    wf_delays = scraper_cfg(config).playwright_delays.get(
        "wellfound", PlaywrightDelays()
    )
    page_load_ms = wf_delays.page_load_ms
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    known_urls = _known_urls_from_config(config)
    skipped_known = 0

    try:
        with _playwright_browser(config) as page:
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                url = f"https://wellfound.com/jobs?q={encoded}&remote=true"
                try:
                    # networkidle never resolves on Wellfound's SPA — use domcontentloaded
                    # then a fixed wait for React to hydrate the job listings.
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(page_load_ms)
                except Exception as exc:
                    log.warning("[wellfound] navigation failed for %r: %s", term, exc)
                    continue

                # Wellfound uses hashed CSS class names — try multiple selectors
                cards = (
                    page.query_selector_all("[class*='styles_jobListing']")
                    or page.query_selector_all("[data-test='StartupResult']")
                    or page.query_selector_all("div[class*='JobListing']")
                )

                for card in cards:
                    try:
                        title_el = (
                            card.query_selector("[class*='jobTitle']")
                            or card.query_selector("h2")
                            or card.query_selector("h3")
                        )
                        company_el = card.query_selector(
                            "[class*='companyName']"
                        ) or card.query_selector("[class*='startupName']")
                        # Location: Wellfound cards surface location near the role
                        # title with hashed class names containing 'location'.
                        loc_el = (
                            card.query_selector("[class*='location']")
                            or card.query_selector("[class*='Location']")
                            or card.query_selector("[class*='jobLocation']")
                        )
                        # Compensation: salary range displayed as "$X-$Y/yr" in a
                        # span/div whose hashed class contains 'compensation' or 'salary'.
                        comp_el = (
                            card.query_selector("[class*='compensation']")
                            or card.query_selector("[class*='Compensation']")
                            or card.query_selector("[class*='salary']")
                            or card.query_selector("[class*='Salary']")
                        )
                        link_el = card.query_selector("a[href*='/jobs/']")
                        if not title_el:
                            continue
                        role = title_el.inner_text().strip()
                        company = company_el.inner_text().strip() if company_el else ""
                        # Empty string fallback — the pipeline accepts empty location
                        # as potentially remote; callers must not assume any default.
                        location = loc_el.inner_text().strip() if loc_el else ""
                        if not matches_roles(role, roles, config):
                            continue
                        # Filter before queuing detail enrichment to avoid Claude
                        # calls on jobs that will be dropped at the post-scrape stage.
                        if not location_matches({"location": location}, config):
                            continue
                        comp = comp_el.inner_text().strip() if comp_el else ""
                        href = link_el.get_attribute("href") if link_el else ""
                        if href and not href.startswith("http"):
                            href = f"https://wellfound.com{href}"
                        if href in seen_urls:
                            continue
                        if href and href in known_urls:
                            skipped_known += 1
                            continue
                        if href:
                            seen_urls.add(href)
                        job: dict = {
                            "company": company,
                            "role": role,
                            "location": location,
                            "url": href,
                            "source": "Wellfound",
                            "date_found": today().isoformat(),
                        }
                        if comp:
                            job["comp"] = comp
                        jobs.append(job)
                    except Exception as exc:
                        log.debug("[wellfound] parse error on card: %s", exc)
                        continue
    except Exception as exc:
        log.warning("[wellfound] browser session error: %s", exc)

    log.info(
        "[wellfound] %d jobs matched (%d skipped as already-known)",
        len(jobs),
        skipped_known,
    )
    return jobs


def scrape_apple(config: dict) -> list[dict]:
    """Playwright scraper for Apple's careers search via internal JSON API.

    Apple's jobs site is a React SPA. Server-rendered HTML shows generic
    results; the real search only fires when client JS submits a POST to
    /api/v1/search. We drive the search input with Playwright and intercept
    the API response to get structured JSON (title, location, team, date).

    Scrolls down after the initial search to trigger additional API pages
    (the SPA fires more /api/v1/search POSTs on scroll). max_pages controls
    how many scroll passes per search term.

    Iterates each configured country's locale x search term.
    """
    cfg = scraper_cfg(config)
    roles = roles_list(config)
    terms = _search_terms(config)
    timeout_ms = timeout_seconds(config) * 1000
    max_pages = cfg.max_pages
    jobs: list[dict] = []
    seen_positions: set[str] = set()
    known_urls = _known_urls_from_config(config)
    skipped_known = 0
    base_url = "https://jobs.apple.com"

    try:
        with _playwright_browser(config) as page:
            for country in countries_list(config):
                params = country_params(country)
                if not params:
                    continue
                locale = params["apple_locale"]

                # Navigate once per locale; reuse the page for each term
                url = f"{base_url}/{locale}/search"
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                    page.wait_for_timeout(1500)
                except Exception as exc:
                    log.warning("[apple] navigation failed for %s: %s", locale, exc)
                    continue

                for term in terms:
                    # Fresh list each iteration; closure captures the name, not
                    # the value, so each _capture_response writes to its own list.
                    api_results: list[Any] = []

                    def _capture_response(response: Any) -> None:
                        if "jobs.apple.com/api/v1/search" in response.url:
                            try:
                                body = response.json()
                                api_results.extend(
                                    body.get("res", {}).get("searchResults", [])
                                )
                            except Exception as exc:
                                log.debug("[apple] response parse failed: %s", exc)

                    page.on("response", _capture_response)
                    try:
                        search_input = page.query_selector(
                            "input.search-typeahead-input"
                        )
                        if not search_input:
                            log.warning("[apple] search input not found for %s", locale)
                            break

                        search_input.click()
                        search_input.fill("")
                        page.wait_for_timeout(200)
                        search_input.fill(term)
                        page.wait_for_timeout(500)
                        search_input.press("Enter")
                        page.wait_for_timeout(4000)

                        # Scroll to trigger additional API pages
                        for _ in range(max_pages - 1):
                            prev_count = len(api_results)
                            page.evaluate(
                                "window.scrollTo(0, document.body.scrollHeight)"
                            )
                            page.wait_for_timeout(2000)
                            if len(api_results) == prev_count:
                                break
                    except Exception as exc:
                        log.warning(
                            "[apple] search failed for %r (%s): %s", term, country, exc
                        )
                        continue
                    finally:
                        page.remove_listener("response", _capture_response)

                    for item in api_results:
                        try:
                            title = item.get("postingTitle", "")
                            if not title or not matches_roles(title, roles, config):
                                continue
                            position_id = item.get("positionId", "") or item.get(
                                "id", ""
                            )
                            if not position_id:
                                continue
                            if position_id in seen_positions:
                                continue
                            seen_positions.add(position_id)
                            locs = item.get("locations", [])
                            location = (
                                locs[0].get("name", "Various") if locs else "Various"
                            )
                            job_id = item.get("id", position_id)
                            slug = item.get("transformedPostingTitle", "")
                            detail_url = f"{base_url}/{locale}/details/{job_id}/{slug}"
                            if detail_url in known_urls:
                                skipped_known += 1
                                continue
                            jobs.append(
                                {
                                    "company": "Apple",
                                    "role": title,
                                    "location": location,
                                    "url": detail_url,
                                    "source": "Apple Careers",
                                    "date_found": today().isoformat(),
                                }
                            )
                        except Exception as exc:
                            log.debug("[apple] parse error on result: %s", exc)
                            continue

                    log.debug(
                        "[apple] %s/%s: %d API results, %d matched",
                        locale,
                        term,
                        len(api_results),
                        len(jobs),
                    )
    except Exception as exc:
        log.warning("[apple] browser session error: %s", exc)

    log.info(
        "[apple] %d jobs matched (%d skipped as already-known)",
        len(jobs),
        skipped_known,
    )
    return jobs


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
