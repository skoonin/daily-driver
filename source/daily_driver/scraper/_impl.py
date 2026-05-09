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
import csv
import json
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
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from daily_driver.scraper.models import (
        EnrichedJob,
        NormalizedJob,
        RawScrapedJob,
    )

if sys.platform != "win32":
    import fcntl

import requests
import yaml
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from daily_driver.core.clock import today
from daily_driver.core.config_models import JobSearchPlugin, Locations, ScraperConfig
from daily_driver.integrations import claude_cli


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

# Per-scraper parameters for each supported country. Add a row here when
# introducing a new country code in .dd-config.yaml.
#
# Keys:
#   apple_locale          - path segment for jobs.apple.com/{locale}/search
#   linkedin_location     - value for LinkedIn's ?location= query param
#   indeed_host           - hostname for Indeed's regional site
COUNTRY_MAP: dict[str, dict[str, str]] = {
    "US": {
        "apple_locale": "en-us",
        "linkedin_location": "United States",
        "indeed_host": "www.indeed.com",
    },
    "CA": {
        "apple_locale": "en-ca",
        "linkedin_location": "Canada",
        "indeed_host": "ca.indeed.com",
    },
    "GB": {
        "apple_locale": "en-gb",
        "linkedin_location": "United Kingdom",
        "indeed_host": "uk.indeed.com",
    },
    "IE": {
        "apple_locale": "en-ie",
        "linkedin_location": "Ireland",
        "indeed_host": "ie.indeed.com",
    },
    "AU": {
        "apple_locale": "en-au",
        "linkedin_location": "Australia",
        "indeed_host": "au.indeed.com",
    },
    "ZA": {
        "apple_locale": "en-za",
        "linkedin_location": "South Africa",
        "indeed_host": "za.indeed.com",
    },
}

# Country display names and common aliases for location matching.
# Used by location_matches() to check if a job's location text belongs to
# an allowed country. ISO codes are excluded to avoid false positives
# (e.g., "CA" matching California instead of Canada).
COUNTRY_NAMES: dict[str, list[str]] = {
    "US": ["United States", "USA"],
    "CA": ["Canada"],
    "GB": ["United Kingdom", "UK", "England", "Scotland", "Wales"],
    "IE": ["Ireland"],
    "AU": ["Australia"],
    "ZA": ["South Africa"],
}


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


def country_params(country: str) -> dict[str, str]:
    """Look up per-scraper parameters for an ISO country code.

    Returns {} and logs a warning for unknown codes so the caller can skip
    that country without crashing the run.
    """
    params = COUNTRY_MAP.get(country.upper())
    if params is None:
        log.warning("[country] unknown country code %r, skipping", country)
        return {}
    return params


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


# ── Company enrichment ───────────────────────────────────────────────────────


def enrich_company_descriptions(
    jobs: list[dict], config: dict | None = None, *, budget: int = 0
) -> dict:
    """Populate Product/Purpose and GD Rating in-place using the Claude CLI.

    One `claude -p` call per unique company name; results cached within the run.
    Logs a warning if the `claude` CLI is not on PATH.

    Budget limits total claude calls to avoid silent stalls on slow networks —
    each call blocks for up to enrich_timeout seconds (default 30s).

    Returns a stats dict with keys: enriched, skipped_cached, failed.
    """
    stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
    if shutil.which("claude") is None:
        log.warning("[enrich] claude CLI not found on PATH, skipping product lookup")
        return stats

    cfg = scraper_cfg(config)
    if budget <= 0:
        budget = cfg.max_enrich_companies
    include_gd = cfg.enrich_gd_rating

    unique_companies = {
        job.get("company", "").strip()
        for job in jobs
        if not job.get("product") and job.get("company", "").strip()
    }
    log.info(
        "[enrich] enriching up to %d companies (%d unique)...",
        budget,
        len(unique_companies),
    )

    cache: dict[str, dict[str, str]] = {}
    calls_made = 0
    budget_warned = False

    for job in jobs:
        if job.get("product"):
            stats["skipped_cached"] += 1
            continue
        company = job.get("company", "").strip()
        if not company:
            continue
        if company not in cache:
            if calls_made >= budget:
                if not budget_warned:
                    remaining = len(
                        {
                            j.get("company", "").strip()
                            for j in jobs
                            if not j.get("product")
                            and j.get("company", "").strip()
                            and j.get("company", "").strip() not in cache
                        }
                    )
                    log.warning(
                        "[enrich] budget reached (%d), %d companies unenriched",
                        budget,
                        remaining,
                    )
                    budget_warned = True
                cache[company] = {"product": "", "gd_rating": ""}
            else:
                if include_gd:
                    prompt = (
                        f"Answer in exactly 2 lines, no preamble:\n"
                        f"Line 1: What does {company} build or do? (max 12 words)\n"
                        f"Line 2: Glassdoor rating (e.g. 4.1), or 'unknown' if unsure."
                    )
                else:
                    prompt = (
                        f"In one sentence (max 12 words), what does {company} build or do? "
                        "Answer only, no preamble."
                    )
                try:
                    stdout = claude_cli.invoke(
                        prompt,
                        headless=True,
                        session_persistence=False,
                        timeout=enrich_timeout(config),
                    )
                    lines = [ln for ln in stdout.splitlines() if ln.strip()]
                    product = lines[0] if lines else ""
                    gd_rating = ""
                    if include_gd and len(lines) >= 2:
                        raw = lines[1].strip().lower()
                        if raw == "unknown":
                            gd_rating = "unknown"
                        else:
                            try:
                                gd_rating = str(round(float(raw), 1))
                            except ValueError:
                                # Claude occasionally returns prose; extract any float in 0.0-5.9 range.
                                m = re.search(r"([0-5]\.\d)", lines[1].strip())
                                if m:
                                    gd_rating = m.group(1)
                                    log.info(
                                        "[enrich] GD rating fallback regex extracted %s for %s",
                                        gd_rating,
                                        company,
                                    )
                                else:
                                    gd_rating = ""
                    cache[company] = {"product": product, "gd_rating": gd_rating}
                except subprocess.CalledProcessError as exc:
                    stderr_tail = (exc.stderr or "").strip()[-200:]
                    log.warning(
                        "[enrich] company=%s rc=%d stderr=%r",
                        company,
                        exc.returncode,
                        stderr_tail,
                    )
                    cache[company] = {"product": "", "gd_rating": ""}
                    stats["failed"] += 1
                except subprocess.TimeoutExpired:
                    log.warning(
                        "[enrich] %s: claude CLI timed out after %ds",
                        company,
                        enrich_timeout(config),
                    )
                    cache[company] = {"product": "", "gd_rating": ""}
                    stats["failed"] += 1
                except (OSError, claude_cli.ClaudeNotFoundError) as exc:
                    log.warning("[enrich] %s lookup failed: %s", company, exc)
                    cache[company] = {"product": "", "gd_rating": ""}
                    stats["failed"] += 1
                calls_made += 1
        cached = cache.get(company, {})
        if cached.get("product"):
            job["product"] = cached["product"]
            stats["enriched"] += 1
        if cached.get("gd_rating") and not job.get("gd_rating"):
            job["gd_rating"] = cached["gd_rating"]
    return stats


def _location_summary(config: dict[str, Any]) -> str:
    loc_cfg = locations_config(config)
    parts = [f"Based in: {home_city(config)}"]
    if loc_cfg is None:
        parts.append("Remote: yes")
        return "; ".join(parts)
    if loc_cfg.cities:
        parts.append("Preferred cities: " + ", ".join(loc_cfg.cities))
    if loc_cfg.countries:
        names = [COUNTRY_NAMES.get(c.upper(), [c])[0] for c in loc_cfg.countries]
        parts.append("Countries: " + ", ".join(names))
    if loc_cfg.remote:
        parts.append("Remote: yes")
    return "; ".join(parts)


def enrich_fit_and_notes(jobs: list[dict], config: dict, *, budget: int = 0) -> dict:
    """Populate Fit score and Notes in-place for new jobs via a single Claude CLI call per job.

    One `claude -p` call per job returns strict JSON: {"fit": <int 1-10>, "notes": "<string>"}.
    This replaces the former enrich_fit + enrich_notes pair, cutting per-job Claude spend by ~33%.

    Budget applies to the combined call count. Previously enrich_fit and enrich_notes each had
    their own budget (max_enrich_fit, max_enrich_notes). The combined budget uses max_enrich_fit
    as the limit (configurable); set it to the combined limit you want.

    Description handling: fit is scored from role/company/location alone, so description is
    optional. When description_text is absent, notes is set to "" (not confabulated). The
    skipped_no_desc counter is always 0 (fit is still scored when description is missing); the
    key is retained for shape compatibility with the former per-enricher stats.

    JSON contract expected from Claude stdout:
        {"fit": 7, "notes": "Kubernetes-heavy SRE; remote CA/US; no comp listed"}
    On description absent:
        {"fit": 7, "notes": ""}
    On insufficient info:
        {"fit": 50, "notes": ""}

    Example prompt response for "Staff SRE at Acme, Remote":
        {"fit": 8, "notes": "AWS/k8s; fully remote; no comp range listed"}

    Returns a stats dict: {"enriched": N, "skipped_budget": N, "skipped_no_desc": 0, "failed": N}.
    """
    stats = {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}
    if shutil.which("claude") is None:
        log.warning("[enrich-fit-notes] claude CLI not found on PATH, skipping")
        return stats

    cfg = scraper_cfg(config)
    if not cfg.enrich_fit or not cfg.enrich_notes:
        log.debug("[enrich-fit-notes] fit or notes disabled via config")
        return stats

    if budget <= 0:
        budget = cfg.max_enrich_fit
    loc_summary = _location_summary(config)
    role_persona = persona(config)
    hc = home_city(config)

    calls_made = 0
    for idx, job in enumerate(jobs):
        if job.get("status") == "skipped":
            continue
        if job.get("fit") and job.get("notes"):
            continue
        if calls_made >= budget:
            remaining = sum(
                1
                for j in jobs[idx:]
                if j.get("status") != "skipped"
                and not (j.get("fit") and j.get("notes"))
            )
            log.warning(
                "[enrich-fit-notes] budget reached (%d), skipping %d jobs",
                budget,
                remaining,
            )
            stats["skipped_budget"] += remaining
            break

        role = job.get("role", "unknown")
        company = job.get("company", "unknown")
        location = job.get("location", "unknown")
        product = job.get("product", "")
        desc = job.get("description_text", "")

        desc_section = ""
        if desc:
            words = desc.split()
            if len(words) > 500:
                desc = " ".join(words[:500]) + " ..."
            desc_section = f"\nDescription: {desc}"

        prompt = (
            f"You are evaluating a job for a {role_persona}.\n"
            f"Location preferences: {loc_summary}\n"
            f"Job: {role} at {company}, {location}\n"
        )
        if product:
            prompt += f"Company: {product}\n"
        prompt += (
            f"{desc_section}\n\n"
            "Reply with ONLY valid JSON on a single line, exactly this shape:\n"
            '{"fit": <integer 1-10>, "notes": "<one line, max 25 words>"}\n\n'
            "fit: rate 1-10 how well this role fits the candidate based on role/company/location. "
            "If you truly lack enough information to assess, return 5.\n"
            "notes: one-line summary including key tech stack, remote policy "
            f"(e.g. 'Remote US-only', 'Hybrid {hc}', 'Remote {hc} OK'), red flags. "
            "If description is absent, return notes as empty string. "
            "Do not guess or hallucinate notes when you have no description.\n"
            "Do not include any preamble, explanation, or markdown -- only the JSON object."
        )

        try:
            stdout = claude_cli.invoke(
                prompt,
                headless=True,
                session_persistence=False,
                timeout=enrich_timeout(config),
            )
            raw = stdout.strip()
            try:
                parsed = json.loads(raw)
                fit_val = parsed.get("fit")
                notes_val = parsed.get("notes", "")
                if isinstance(fit_val, (int, float)):
                    score = min(int(fit_val), 10)
                    if not job.get("fit"):
                        job["fit"] = f"{score}/10"
                    if not job.get("notes") and isinstance(notes_val, str):
                        job["notes"] = notes_val
                    stats["enriched"] += 1
                else:
                    log.warning(
                        "[enrich-fit-notes] company=%s role=%s: JSON missing valid fit field: %r",
                        company,
                        role,
                        raw[:200],
                    )
                    stats["failed"] += 1
            except json.JSONDecodeError:
                log.warning(
                    "[enrich-fit-notes] company=%s role=%s: non-JSON response: %r",
                    company,
                    role,
                    raw[:200],
                )
                stats["failed"] += 1
        except subprocess.CalledProcessError as exc:
            stderr_tail = (exc.stderr or "").strip()[-200:]
            log.warning(
                "[enrich-fit-notes] company=%s role=%s rc=%d stderr=%r",
                company,
                role,
                exc.returncode,
                stderr_tail,
            )
            stats["failed"] += 1
        except subprocess.TimeoutExpired:
            log.warning(
                "[enrich-fit-notes] %s: claude CLI timed out after %ds",
                company,
                enrich_timeout(config),
            )
            stats["failed"] += 1
        except (OSError, claude_cli.ClaudeNotFoundError) as exc:
            log.warning("[enrich-fit-notes] %s failed: %s", company, exc)
            stats["failed"] += 1
        calls_made += 1
    return stats


# ── Job detail enrichment ────────────────────────────────────────────────────

# Currency code → display prefix. Anything not listed falls through to the
# raw code prefixed with a space (e.g. "EUR 100,000/yr").
_COMP_CURRENCY_PREFIX = {
    "USD": "$",
    "CAD": "CA$",
    "GBP": "\u00a3",
    "EUR": "\u20ac",
}

# Schema.org unitText → suffix. JobPosting commonly uses YEAR/MONTH/HOUR.
_COMP_UNIT_SUFFIX = {
    "YEAR": "/yr",
    "MONTH": "/mo",
    "WEEK": "/wk",
    "DAY": "/day",
    "HOUR": "/hr",
}


def _format_comp(base_salary: dict) -> str:
    """Render a JSON-LD MonetaryAmount into a short human string.

    Returns "" for any shape that doesn't yield at least one numeric value —
    callers treat empty as "no comp data" and leave the CSV column blank.
    """
    if not isinstance(base_salary, dict):
        return ""
    currency = (base_salary.get("currency") or "").strip().upper()
    value = base_salary.get("value")

    # value can be a QuantitativeValue dict or a bare number/string
    if isinstance(value, dict):
        min_v = value.get("minValue")
        max_v = value.get("maxValue")
        single = value.get("value")
        unit = (value.get("unitText") or "").strip().upper()
    else:
        min_v = max_v = None
        single = value
        unit = (base_salary.get("unitText") or "").strip().upper()

    lo, hi, mid = _to_int(min_v), _to_int(max_v), _to_int(single)
    if lo is not None and hi is not None and lo != hi:
        amount = f"{lo:,}\u2013{hi:,}"
    elif mid is not None:
        amount = f"{mid:,}"
    elif lo is not None:
        amount = f"{lo:,}"
    else:
        return ""

    prefix = _COMP_CURRENCY_PREFIX.get(currency)
    if prefix is None:
        # Unknown currency: keep the code so the user can see what it is.
        prefix = f"{currency} " if currency else ""
    suffix = _COMP_UNIT_SUFFIX.get(unit, "")
    return f"{prefix}{amount}{suffix}"


def _find_jobposting(node: Any) -> dict | None:
    """Walk a parsed JSON-LD payload and return the first JobPosting dict."""
    if isinstance(node, dict):
        node_type = node.get("@type")
        if node_type == "JobPosting" or (
            isinstance(node_type, list) and "JobPosting" in node_type
        ):
            return node
        # @graph wraps a list of nodes in some payloads
        graph = node.get("@graph")
        if isinstance(graph, list):
            for child in graph:
                found = _find_jobposting(child)
                if found is not None:
                    return found
    elif isinstance(node, list):
        for child in node:
            found = _find_jobposting(child)
            if found is not None:
                return found
    return None


def enrich_job_details(jobs: list[dict], config: dict) -> None:
    """Fetch each job's detail page and populate comp/posted_date in place.

    Caches by URL within the run so jobs that share a detail URL only generate
    one HTTP request. Skips jobs that already have `comp` set so listing-card
    sources that already provide salary aren't clobbered. Network/parse errors
    are swallowed — missing data is the expected outcome for boards that don't
    expose JSON-LD, not an error worth aborting the run for.
    """
    cache: dict[str, dict] = {}
    cfg = scraper_cfg(config)
    delay = cfg.detail_delay_seconds
    timeout_s = timeout_seconds(config)
    headers = {"User-Agent": user_agent(config)}

    fetched_count = 0
    enriched_count = 0
    for job in jobs:
        if job.get("comp"):
            continue
        if job.get("status") == "skipped":
            continue
        url = (job.get("url") or "").strip()
        if not url:
            continue
        # LinkedIn anonymous detail pages don't emit JSON-LD; comp/description
        # arrive via JobSpy's linkedin_fetch_description. Skip the GET entirely
        # rather than fetch HTML only to throw it away.
        if "linkedin.com" in url:
            continue

        if url in cache:
            details = cache[url]
        else:
            if fetched_count > 0 and delay > 0:
                time.sleep(delay)
            fetched_count += 1
            try:
                resp = requests.get(url, headers=headers, timeout=timeout_s)
                resp.raise_for_status()
                details = _parse_detail_page(resp.text, url)
            except requests.RequestException as exc:
                # Network flakes are expected and non-fatal — missing comp just
                # means the CSV column stays blank. Programmer errors from the
                # parser path (AttributeError, TypeError, etc.) are deliberately
                # NOT caught here; we want them visible, not silently swallowed.
                log.debug("[detail] %s: fetch failed: %s", url, exc)
                details = {}
            cache[url] = details

        if details.get("comp"):
            job["comp"] = details["comp"]
            enriched_count += 1
        if details.get("posted_date") and not job.get("posted_date"):
            job["posted_date"] = details["posted_date"]
        if details.get("description_text") and not job.get("description_text"):
            job["description_text"] = details["description_text"]

    log.info(
        "[detail] fetched %d pages, enriched %d of %d jobs",
        fetched_count,
        enriched_count,
        len(jobs),
    )


# K4: salary parsing now lives in models._parse_k_salary.
from daily_driver.scraper.models import _parse_k_salary  # noqa: E402


def parse_greenhouse_html(html: str) -> dict:
    """Extract comp from a Greenhouse job-boards page.

    Greenhouse pages don't emit JSON-LD. The salary, when present, lives in
    a paragraph or list item containing a literal 'Annual Salary:' prefix on
    Anthropic's board (as of 2026-04-11). We match on that prefix so we don't
    accidentally grab unrelated dollar amounts elsewhere on the page.
    """
    if not html:
        return {}
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # pragma: no cover - defensive only
        log.debug("[enrich] BeautifulSoup failed (greenhouse): %s", exc)
        return {}

    out = {}

    # Job description text for downstream Notes enrichment.
    desc_div = soup.find("div", id="content") or soup.find(
        "div", class_="job-description"
    )
    if desc_div:
        out["description_text"] = desc_div.get_text(" ", strip=True)

    text = soup.get_text(" ", strip=True)
    m = re.search(
        r"Annual Salary:\s*" r"(?P<p>\$|US\$|CA\$|[A-Z]{3}\s?)"
        # K/M suffix: Greenhouse boards like Anthropic post "$150K" shorthand
        r"(?P<mraw>[\d,]+[KkMm]?)"
        r"\s*[-\u2013\u2014]\s*"
        r"(?P<p2>\$|US\$|CA\$|[A-Z]{3}\s?)?"
        r"(?P<xraw>[\d,]+[KkMm]?)"
        r"(?:\s*(?P<cur>USD|CAD|GBP|EUR))?",
        text,
    )
    if m:
        lo = _parse_k_salary(m["mraw"])
        hi = _parse_k_salary(m["xraw"])
        if lo is not None and hi is not None:
            currency_suffix = f" {m['cur']}" if m["cur"] else ""
            max_prefix = m["p2"] or m["p"]
            out["comp"] = (
                f"{m['p']}{lo:,}\u2013{max_prefix}{hi:,}/yr{currency_suffix}".strip()
            )

    return out


def _parse_detail_page(html: str, url: str) -> dict:
    """Dispatch to the right detail-page parser based on URL hostname.

    Greenhouse job-boards pages lack JSON-LD; we try JSON-LD first
    (covers any Greenhouse board that does publish structured data) and fall
    back to the text-pattern parser. Everything else uses JSON-LD only.
    LinkedIn descriptions/comp now come from JobSpy's
    ``linkedin_fetch_description=True``; the vendored HTML parser was deleted.
    """
    host = urllib.parse.urlparse(url).hostname or ""
    if "greenhouse.io" in host:
        result = parse_jsonld_jobposting(html)
        if result.get("comp"):
            return result
        return parse_greenhouse_html(html) or result
    return parse_jsonld_jobposting(html)


def parse_jsonld_jobposting(html: str) -> dict:
    """Extract job details from JSON-LD JobPosting blocks in an HTML page.

    Returns a dict with any of: comp, posted_date, employment_type. Returns an
    empty dict if no JobPosting block is present or all blocks fail to parse —
    callers must treat missing keys as "data not available", not as errors.
    """
    if not html:
        return {}

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # pragma: no cover - defensive only
        log.debug("[enrich] BeautifulSoup failed: %s", exc)
        return {}

    posting: dict | None = None
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Leave a breadcrumb: if a site's JSON-LD is consistently truncated
            # or malformed we want some trace of it in debug logs rather than
            # silent data loss across every run.
            log.debug("[enrich] skipping malformed JSON-LD block: %s", exc)
            continue
        posting = _find_jobposting(payload)
        if posting is not None:
            break

    if posting is None:
        return {}

    out: dict = {}
    base_salary = posting.get("baseSalary")
    if base_salary is not None:
        comp = _format_comp(base_salary if isinstance(base_salary, dict) else {})
        if comp:
            out["comp"] = comp
        elif isinstance(base_salary, (dict, str)):
            # baseSalary is present but didn't yield a number (e.g. a bare
            # "Competitive" string, or a MonetaryAmount we don't understand).
            # Log so a future site format change is diagnosable — callers
            # treat a missing "comp" key as "no data".
            log.debug("[enrich] baseSalary present but unparseable: %r", base_salary)

    posted = posting.get("datePosted")
    if isinstance(posted, str) and posted:
        # Take the date portion of an ISO-8601 timestamp; tolerate "YYYY-MM-DD"
        # already-stripped values.
        out["posted_date"] = posted[:10]

    employment = posting.get("employmentType")
    if isinstance(employment, str):
        out["employment_type"] = employment
    elif isinstance(employment, list) and employment:
        out["employment_type"] = str(employment[0])

    desc_html = posting.get("description", "")
    if desc_html:
        out["description_text"] = BeautifulSoup(desc_html, "html.parser").get_text(
            " ", strip=True
        )

    return out


# ── CSV helpers ───────────────────────────────────────────────────────────────

CANONICAL_HEADER = [
    "Status",
    "Notes",
    "Company",
    "Location",
    "Role",
    "Fit",
    "Comp",
    "Date Found",
    # Date Last Seen drives `jobs prune --older-than`. Today scraper
    # only sets it on insert (defaults to Date Found); the upsert-on-rescan
    # path is owned by W5/W6 — until then prune ages from first-discovery.
    "Date Last Seen",
    "Date Applied",
    "Link",
    "Product/Purpose",
    "GD Rating",
    "Source",
]


def _migrate_legacy_header(csv_path: Path, current_header: list[str]) -> list[str]:
    """Rewrite jobs.csv from the legacy '#'-first header to the new layout.

    Creates jobs.csv.bak.<timestamp> so the migration is reversible. Idempotent:
    if the header is already current, this is a no-op and no backup is written.
    """
    if current_header == CANONICAL_HEADER:
        return CANONICAL_HEADER

    backup = csv_path.with_suffix(f".csv.bak.{int(time.time())}")
    shutil.copy2(csv_path, backup)
    log.info("[migrate] jobs.csv backed up to %s", backup.name)

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CANONICAL_HEADER,
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            row.pop("#", None)
            writer.writerow(row)
    log.info("[migrate] jobs.csv rewritten to new column layout")
    return CANONICAL_HEADER


def load_existing_jobs(csv_path: Path) -> tuple[set[str], set[str], list[str]]:
    """Return (known_urls, known_keys, header_columns).

    known_urls  — set of Link column values, for URL-based dedup.
    known_keys  — set of dedup_key(company, role) strings, for cross-site dedup.
    """
    if not csv_path.exists():
        return set(), set(), []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                return set(), set(), []
            if "Link" not in header:
                raise ScraperError(
                    "jobs.csv is missing required 'Link' column — cannot deduplicate"
                )
            link_idx = header.index("Link")
            company_idx = header.index("Company") if "Company" in header else None
            role_idx = header.index("Role") if "Role" in header else None
            known_urls: set[str] = set()
            known_keys: set[str] = set()
            for row in reader:
                if not row:
                    continue
                if link_idx < len(row) and row[link_idx]:
                    known_urls.add(row[link_idx].strip())
                company = (
                    row[company_idx].strip()
                    if company_idx is not None and company_idx < len(row)
                    else ""
                )
                role = (
                    row[role_idx].strip()
                    if role_idx is not None and role_idx < len(row)
                    else ""
                )
                if company or role:
                    known_keys.add(dedup_key(company, role))
    except OSError as exc:
        raise ScraperError(f"Cannot read {csv_path}: {exc}") from exc
    return known_urls, known_keys, header


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


def _parse_comp(comp_str: str) -> dict:
    """Legacy dict-shape wrapper around ``Comp.parse``.

    Kept for the dict-based pipeline and ``comp_meets_threshold``. Returns
    ``comp_min_native``, ``comp_max_native``, ``comp_currency``, ``comp_period``,
    ``comp_min_usd``, ``comp_max_usd``. All ``None`` / ``""`` on unparseable input.
    Collapses into ``Comp.parse`` direct calls at K8/K9.
    """
    from daily_driver.scraper.models import Comp

    c = Comp.parse(comp_str)
    if not c.is_known:
        return {
            "comp_min_native": None,
            "comp_max_native": None,
            "comp_currency": "",
            "comp_period": "",
            "comp_min_usd": None,
            "comp_max_usd": None,
        }
    return {
        "comp_min_native": c.min_native,
        "comp_max_native": c.max_native,
        "comp_currency": c.currency or "",
        "comp_period": c.period,
        "comp_min_usd": c.min_usd,
        "comp_max_usd": c.max_usd,
    }


def comp_meets_threshold(job: dict, config: dict) -> tuple[bool, str]:
    """Return (True, "") if comp is acceptable or unknown; (False, reason) if below threshold.

    Fails open when comp_max_usd is absent or zero so roles with no listed
    comp reach CSV for manual review rather than being silently dropped.
    """
    threshold = min_comp_usd(config)
    cmax = job.get("comp_max_usd")
    if not cmax:
        return (True, "")
    if cmax >= threshold:
        return (True, "")
    return (False, f"below comp threshold (max ${cmax:,} < ${threshold:,})")


def currency_matches_primary(job: dict, config: dict) -> bool:
    """Return True if ``job`` should pass the primary-currency filter (#48).

    When ``plugins.job_search.primary_currency`` is unset, every job passes.
    When set, jobs with a parsed ``comp_currency`` matching pass; jobs with an
    empty/missing currency (unparseable comp) also pass — currency=None is the
    sentinel for "couldn't read", not "doesn't match".
    """
    primary = _model(config).primary_currency
    if primary is None:
        return True
    job_currency = job.get("comp_currency") or ""
    if not job_currency:
        return True
    return job_currency == primary


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


def append_jobs(csv_path: Path, jobs: list[dict], header: list[str]) -> int:
    """Append new jobs to CSV. Returns count of rows written.

    Legacy dict-based entry point. Typed callers should use
    ``append_jobs_typed`` (K6) which routes through
    ``EnrichedJob.to_csv_row()``.
    """
    if not jobs:
        return 0

    written = 0
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            if sys.platform != "win32":
                fcntl.flock(f, fcntl.LOCK_EX)
            writer = csv.DictWriter(
                f, fieldnames=header, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
            )
            for job in jobs:
                row = {col: "" for col in header}
                row["Company"] = job.get("company", "")
                row["Product/Purpose"] = job.get(
                    "product", "(auto-scraped -- needs fill)"
                )
                row["Role"] = job.get("role", "")
                row["Comp"] = job.get("comp", "")
                row["Location"] = job.get("location", "")
                row["Source"] = job.get("source", "")
                row["Date Found"] = job.get("date_found", today().isoformat())
                row["Date Last Seen"] = job.get("date_last_seen", row["Date Found"])
                row["Status"] = job.get("status") or "found"
                row["Link"] = job.get("url", "")
                row["Fit"] = job.get("fit", "")
                row["GD Rating"] = job.get("gd_rating", "")
                row["Notes"] = job.get("notes", "")
                writer.writerow(row)
                written += 1
    except OSError as exc:
        raise ScraperError(f"Cannot open {csv_path} for writing: {exc}") from exc
    return written


def _enriched_to_dict(job: "EnrichedJob") -> dict[str, Any]:  # noqa: F821
    """Project an EnrichedJob into the legacy working-dict shape.

    Used by typed enricher wrappers so existing dict-based enricher bodies
    (which mutate in place) can run unchanged. K9 collapses these wrappers.
    """
    return {
        "company": job.company,
        "role": job.role,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "source_canonical": job.source_canonical,
        "source_board": job.source_board,
        "comp": str(job.comp),
        "date_found": job.date_found.isoformat(),
        "product": job.product,
        "gd_rating": job.gd_rating,
        "fit": "" if job.fit is None else job.fit,
        "notes": job.notes,
        "description_text": job.description_text,
        "status": job.status.value,
    }


def _dict_to_enriched_updates(d: dict[str, Any]) -> dict[str, Any]:
    """Pick the enricher-mutated fields out of the working dict.

    Returned dict is suitable for ``EnrichedJob.model_copy(update=...)``.
    """
    from daily_driver.scraper.models import Comp, JobStatus

    updates: dict[str, Any] = {}
    if "product" in d:
        updates["product"] = d["product"]
    if "gd_rating" in d:
        updates["gd_rating"] = d["gd_rating"]
    if "fit" in d:
        f = d["fit"]
        updates["fit"] = (
            int(f)
            if isinstance(f, int) or (isinstance(f, str) and f.isdigit())
            else None
        )
    if "notes" in d:
        updates["notes"] = d["notes"]
    if "description_text" in d:
        updates["description_text"] = d["description_text"]
    if "status" in d and d["status"]:
        updates["status"] = JobStatus(d["status"])
    if "skip_reason" in d:
        updates["skip_reason"] = d["skip_reason"]
    if "comp" in d and isinstance(d["comp"], str) and d["comp"]:
        # Re-parse only when the enricher updated the display string.
        new_comp = Comp.parse(d["comp"])
        updates["comp"] = new_comp
    return updates


def enrich_company_descriptions_typed(
    jobs: "list[EnrichedJob]",  # noqa: F821
    config: dict[str, Any] | None = None,
    *,
    budget: int = 0,
) -> tuple["list[EnrichedJob]", dict[str, int]]:  # noqa: F821
    """Typed wrapper around ``enrich_company_descriptions`` (K8).

    Round-trips through a working-dict list because the legacy body mutates
    in place; returns a fresh list of frozen EnrichedJob instances built via
    ``model_copy(update=...)``.
    """
    working = [_enriched_to_dict(j) for j in jobs]
    stats = enrich_company_descriptions(working, config, budget=budget)
    out = [
        j.model_copy(update=_dict_to_enriched_updates(d))
        for j, d in zip(jobs, working, strict=True)
    ]
    return out, stats


def enrich_fit_and_notes_typed(
    jobs: "list[EnrichedJob]",  # noqa: F821
    config: dict[str, Any],
    *,
    budget: int = 0,
) -> tuple["list[EnrichedJob]", dict[str, int]]:  # noqa: F821
    """Typed wrapper around ``enrich_fit_and_notes`` (K8)."""
    working = [_enriched_to_dict(j) for j in jobs]
    stats = enrich_fit_and_notes(working, config, budget=budget)
    out = [
        j.model_copy(update=_dict_to_enriched_updates(d))
        for j, d in zip(jobs, working, strict=True)
    ]
    return out, stats


def enrich_job_details_typed(
    jobs: "list[EnrichedJob]",  # noqa: F821
    config: dict[str, Any],
) -> "list[EnrichedJob]":  # noqa: F821
    """Typed wrapper around ``enrich_job_details`` (K8).

    The legacy ``enrich_job_details`` returns ``None`` and mutates in place;
    this wrapper produces a fresh list with each job's description_text /
    posted_date / comp populated where the detail fetch supplied them.
    """
    import datetime as dt

    working = [_enriched_to_dict(j) for j in jobs]
    enrich_job_details(working, config)
    out: list[Any] = []
    for j, d in zip(jobs, working, strict=True):
        updates = _dict_to_enriched_updates(d)
        # Detail enricher may also write posted_date as ISO string.
        if d.get("posted_date") and isinstance(d["posted_date"], str):
            try:
                updates["posted_date"] = dt.date.fromisoformat(d["posted_date"])
            except ValueError:
                pass
        # Comp from JSON-LD comes back via "comp" string; already handled.
        out.append(j.model_copy(update=updates))
    return out


def append_jobs_typed(
    csv_path: Path,
    jobs: "list[EnrichedJob]",  # noqa: F821
    header: list[str],
) -> int:
    """Typed CSV writer (K6): one row per ``EnrichedJob.to_csv_row()``.

    Header is still passed in so callers can pin the column order to whatever
    is in ``jobs.csv`` today (legacy migrations may have re-ordered columns).
    Extra keys produced by ``to_csv_row`` are dropped via ``extrasaction='ignore'``.
    """
    if not jobs:
        return 0

    written = 0
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            if sys.platform != "win32":
                fcntl.flock(f, fcntl.LOCK_EX)
            writer = csv.DictWriter(
                f, fieldnames=header, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
            )
            for job in jobs:
                writer.writerow(job.to_csv_row())
                written += 1
    except OSError as exc:
        raise ScraperError(f"Cannot open {csv_path} for writing: {exc}") from exc
    return written


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


# ── Shared HTTP helpers ───────────────────────────────────────────────────────


def _http_session(config: dict) -> requests.Session:
    """Build a reusable requests.Session from scraper config."""
    session = requests.Session()
    session.headers["User-Agent"] = user_agent(config)
    return session


def _api_get(
    session: requests.Session,
    url: str,
    config: dict,
    *,
    label: str = "",
) -> requests.Response | None:
    """GET a URL, log + return None on failure instead of raising."""
    try:
        resp = session.get(url, timeout=timeout_seconds(config))
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        log.warning("[%s] request failed for %s: %s", label, url, exc)
        return None


# ── Playwright helpers ────────────────────────────────────────────────────────


def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


@contextmanager
def _playwright_browser(config: dict) -> Any:
    """Yield a Playwright Page with non-headless Chromium and realistic settings.

    Non-headless by default — avoids most bot-detection heuristics on LinkedIn,
    Indeed, and Wellfound without requiring a logged-in session.
    Set job_search.scraper.headless: true in config to run headless.
    """
    if not _has_playwright():
        raise ImportError(
            "playwright not installed — run: pip install playwright && playwright install chromium"
        )

    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    headless = scraper_cfg(config).headless
    ua = user_agent(config)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=headless)
        except (PWError, OSError) as exc:
            log.error(
                "browser launch failed (run: playwright install chromium): %s", exc
            )
            raise
        ctx = browser.new_context(
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = ctx.new_page()
        try:
            yield page
        finally:
            ctx.close()
            browser.close()


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
    jobspy_cfg = cfg.jobspy
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


def run_all_scrapers(config: dict) -> tuple[list[dict], list[str]]:
    """Run all enabled scrapers and deduplicate results within this run.

    Two phases:
      Phase 1 — headless-safe sources run in parallel via ThreadPoolExecutor
      Phase 2 — non-headless sources (wellfound, apple) run serially

    Phase 2 stays serial by design: running multiple visible Chromium windows
    concurrently is RAM-heavy and makes bot detection easier. Deduplicates by
    both URL and company+role key so the same job appearing on multiple boards
    is only kept once (first scraper wins).
    """
    cfg = scraper_cfg(config)
    source_cfg = cfg.sources
    workers = cfg.parallel_workers

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


# ── Backfill ─────────────────────────────────────────────────────────────────

# Column mapping: CSV header name -> internal dict key
_CSV_TO_DICT = {
    "Status": "status",
    "Company": "company",
    "Product/Purpose": "product",
    "Role": "role",
    "Comp": "comp",
    "Location": "location",
    "Fit": "fit",
    "GD Rating": "gd_rating",
    "Source": "source",
    "Date Found": "date_found",
    "Date Applied": "date_applied",
    "Link": "url",
    "Notes": "notes",
}

_DICT_TO_CSV = {v: k for k, v in _CSV_TO_DICT.items()}

_PLACEHOLDER_PRODUCT = "(auto-scraped -- needs fill)"


def _row_to_dict(row: dict[str, str]) -> dict[str, str]:
    """Convert a CSV DictReader row to our internal job dict format."""
    out: dict[str, str] = {}
    for csv_col, dict_key in _CSV_TO_DICT.items():
        val = (row.get(csv_col) or "").strip()
        if dict_key == "product" and val == _PLACEHOLDER_PRODUCT:
            val = ""
        out[dict_key] = val
    return out


def _dict_to_row(job: dict[str, str], header: list[str]) -> dict[str, str]:
    """Convert an internal job dict back to a CSV row dict."""
    row = {col: "" for col in header}
    for dict_key, csv_col in _DICT_TO_CSV.items():
        if csv_col in row:
            row[csv_col] = job.get(dict_key, "")
    return row


def backfill(config: dict, csv_path: Path) -> None:
    """Re-enrich existing jobs.csv rows that have empty enrichment fields."""
    validate_config(config)
    if not csv_path.exists():
        raise ScraperError(f"jobs.csv not found at {csv_path}")

    # Hold LOCK_EX for the entire read-enrich-write cycle to prevent a
    # concurrent scrape run from appending rows that get dropped by our rewrite.
    with open(csv_path, "r+", newline="", encoding="utf-8") as lock_fh:
        if sys.platform != "win32":
            fcntl.flock(lock_fh, fcntl.LOCK_EX)

        reader = csv.DictReader(lock_fh)
        header = reader.fieldnames or CANONICAL_HEADER
        rows = list(reader)

        jobs = [_row_to_dict(r) for r in rows]

        needs_product = sum(1 for j in jobs if not j.get("product"))
        needs_fit = sum(1 for j in jobs if not j.get("fit"))
        needs_gd = sum(1 for j in jobs if not j.get("gd_rating"))
        needs_notes = sum(1 for j in jobs if not j.get("notes"))

        log.info(
            "[backfill] %d rows: %d need Product, %d need GD, %d need Fit, %d need Notes",
            len(jobs),
            needs_product,
            needs_gd,
            needs_fit,
            needs_notes,
        )

        if not (needs_product or needs_fit or needs_gd or needs_notes):
            print("All rows already enriched, nothing to backfill.")
            return

        enrich_company_descriptions(jobs, config, budget=sys.maxsize)
        enrich_fit_and_notes(jobs, config, budget=sys.maxsize)

        # Write to a sibling .tmp first so a crash mid-write leaves the
        # original intact. rename() is atomic on POSIX same-filesystem paths.
        backup = csv_path.with_suffix(f".csv.bak.{int(time.time())}")
        shutil.copy2(csv_path, backup)
        log.info("[backfill] backed up to %s", backup.name)

        tmp_path = csv_path.with_suffix(".csv.tmp")
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=list(header),
                quoting=csv.QUOTE_MINIMAL,
                extrasaction="ignore",
            )
            writer.writeheader()
            for job in jobs:
                writer.writerow(_dict_to_row(job, list(header)))

        tmp_path.rename(csv_path)

    filled_product = needs_product - sum(1 for j in jobs if not j.get("product"))
    filled_fit = needs_fit - sum(1 for j in jobs if not j.get("fit"))
    filled_gd = needs_gd - sum(1 for j in jobs if not j.get("gd_rating"))
    print(
        f"Backfill complete: +{filled_product} Product, +{filled_gd} GD, +{filled_fit} Fit"
    )


# ── Entry point ───────────────────────────────────────────────────────────────
