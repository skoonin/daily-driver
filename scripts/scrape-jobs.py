#!/usr/bin/env python3
"""
Job scraper: scrapes enabled sources and appends new rows to jobs.csv.
Safe to re-run — deduplicates by URL and by (company, role) against existing rows.

Sources:
  remoteok          — Playwright, public listings
  weworkremotely    — Playwright, public listings
  hn_who_is_hiring  — requests + BeautifulSoup (static HTML, no JS required)
  anthropic         — Playwright, careers page
  linkedin          — Playwright, public search (non-headless, best-effort)
  indeed            — Playwright, public search (non-headless, best-effort)
  wellfound         — Playwright, public search (non-headless, best-effort)
  apple             — Playwright, careers search page
"""

import argparse
import copy
import csv
import json
import logging
import time
import re
import subprocess
import sys
import urllib.parse
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import shutil

import requests
import yaml
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

log = logging.getLogger(__name__)

# Two-tier role matching: domain + optional seniority
_DOMAIN_KEYWORDS = {
    "sre", "site reliability", "platform engineer", "platform engineering",
    "devops", "infrastructure", "cloud engineer", "cloud engineering",
    "ci/cd", "build engineer", "release engineer", "production engineer",
}
_SENIORITY_KEYWORDS = {"senior", "staff", "principal", "lead", "sr.", "sr "}

# Seniority prefixes stripped when compressing 21 roles → ~10 search terms
_SENIORITY_PREFIXES = (
    "senior ", "staff ", "principal ", "lead ", "sr. ", "sr ",
)


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_output_dir(config: dict) -> Path:
    raw = config.get("output_dir", "~/git/docs-local/daily-driver")
    return Path(raw).expanduser()


def scraper_cfg(config: dict) -> dict:
    return config.get("job_search", {}).get("scraper", {})


def roles_list(config: dict) -> list[str]:
    return config.get("job_search", {}).get("roles", [])


def user_agent(config: dict) -> str:
    return scraper_cfg(config).get(
        "user_agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )


def timeout_seconds(config: dict) -> int:
    return int(scraper_cfg(config).get("timeout", 15))


# ── Dedup helpers ─────────────────────────────────────────────────────────────

def dedup_key(company: str, role: str) -> str:
    """Normalized dedup key for cross-site duplicate detection.

    Lowercases and collapses whitespace in both fields so the same job posted
    on RemoteOK and LinkedIn produces an identical key.
    """
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.lower().strip())
    return f"{_norm(company)}::{_norm(role)}"


# ── Company enrichment ───────────────────────────────────────────────────────

def enrich_company_descriptions(jobs: list[dict], config: dict | None = None) -> None:
    """Populate Product/Purpose in-place for jobs that lack it, using the Claude CLI.

    One `claude -p` call per unique company name; results cached within the run.
    Silently skips enrichment if the `claude` CLI is not on PATH.

    Budget limits total claude calls to avoid silent stalls on slow networks —
    each call blocks for up to 15s, so 10 companies = max 2.5 min wall time.
    """
    if shutil.which("claude") is None:
        log.debug("[enrich] claude CLI not found, skipping product lookup")
        return

    cfg = scraper_cfg(config) if config else {}
    budget = int(cfg.get("max_enrich_companies", 10))

    unique_companies = {
        job.get("company", "").strip()
        for job in jobs
        if not job.get("product") and job.get("company", "").strip()
    }
    log.info("[enrich] enriching up to %d companies (%d unique)...", budget, len(unique_companies))

    cache: dict[str, str] = {}
    calls_made = 0

    for job in jobs:
        if job.get("product"):
            continue
        company = job.get("company", "").strip()
        if not company:
            continue
        if company not in cache:
            if calls_made >= budget:
                remaining = len([j for j in jobs if not j.get("product") and j.get("company", "").strip() and j.get("company", "").strip() not in cache])
                log.warning("[enrich] budget reached, skipping %d companies", remaining)
                break
            prompt = (
                f"In one sentence (max 12 words), what does {company} build or do? "
                "Answer only, no preamble."
            )
            try:
                result = subprocess.run(
                    ["claude", "-p", prompt],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    lines = [l for l in result.stdout.splitlines() if l.strip()]
                    cache[company] = lines[0] if lines else ""
                else:
                    cache[company] = ""
            except subprocess.TimeoutExpired:
                log.warning("[enrich] %s: claude CLI timed out after 15s", company)
                cache[company] = ""
            except OSError as exc:
                log.debug("[enrich] %s lookup failed: %s", company, exc)
                cache[company] = ""
            calls_made += 1
        if cache.get(company):
            job["product"] = cache[company]


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

    def _num(x) -> int | None:
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return None

    lo, hi, mid = _num(min_v), _num(max_v), _num(single)
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


def _find_jobposting(node) -> dict | None:
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
    delay = float(cfg.get("detail_delay_seconds", 0.5))
    timeout_s = timeout_seconds(config)
    headers = {"User-Agent": user_agent(config)}

    fetched_count = 0
    enriched_count = 0
    for job in jobs:
        if job.get("comp"):
            continue
        url = (job.get("url") or "").strip()
        if not url:
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

    log.info(
        "[detail] fetched %d pages, enriched %d of %d jobs",
        fetched_count, enriched_count, len(jobs),
    )


# LinkedIn renders an already-human-readable range. Example text found on the
# real Fable posting (2026-04-10): "CA$130,000.00/yr - CA$150,000.00/yr". We
# normalize it to match the JSON-LD formatter's output shape: drop .00, collapse
# to an en-dash range with the unit suffix at the end.
#
# Primary: tidy LinkedIn format with leading prefix + explicit unit.
# Example: "CA$130,000.00/yr - CA$150,000.00/yr"
_LINKEDIN_RANGE_RE = re.compile(
    r"^(?P<p>CA\$|US\$|\$|\u00a3|\u20ac|[A-Z]{3}\s?)"
    r"(?P<min>[\d,]+)(?:\.\d+)?"
    r"(?P<u>/\w+)"
    r"\s*[-\u2013\u2014]\s*"       # accept -, en-dash, em-dash
    r"(?P=p)(?P<max>[\d,]+)(?:\.\d+)?(?P=u)$"
)

_LINKEDIN_SINGLE_RE = re.compile(
    r"^(?P<p>CA\$|US\$|\$|\u00a3|\u20ac|[A-Z]{3}\s?)"
    r"(?P<amount>[\d,]+)(?:\.\d+)?"
    r"(?P<u>/\w+)$"
)

# Fallback: looser range with optional unit and trailing currency code.
# Handles postings like "$144,000\u2014$200,000 CAD" where the currency code
# is a suffix rather than a prefix and no unit is present.
_LINKEDIN_LOOSE_RANGE_RE = re.compile(
    r"^(?P<p>CA\$|US\$|\$|\u00a3|\u20ac)"
    r"(?P<min>[\d,]+)(?:\.\d+)?"
    r"\s*[-\u2013\u2014]\s*"
    r"(?:CA\$|US\$|\$|\u00a3|\u20ac)?"
    r"(?P<max>[\d,]+)(?:\.\d+)?"
    r"(?:\s*(?P<cur>USD|CAD|GBP|EUR))?$"
)


def _clean_linkedin_comp(raw: str) -> str:
    """Normalize LinkedIn's comp text into the shape used by the JSON-LD path.

    Returns "" if the text doesn't match a recognized pattern — callers treat
    empty as "could not parse", not an error. We deliberately don't try to
    salvage partial matches: a weird format is better left blank than filled
    with a half-guess.
    """
    s = " ".join((raw or "").split())
    m = _LINKEDIN_RANGE_RE.match(s)
    if m:
        return f"{m['p']}{m['min']}\u2013{m['max']}{m['u']}"
    m = _LINKEDIN_SINGLE_RE.match(s)
    if m:
        return f"{m['p']}{m['amount']}{m['u']}"
    m = _LINKEDIN_LOOSE_RANGE_RE.match(s)
    if m:
        # Default to /yr when the source omits a unit — job-board convention.
        cur = f" {m['cur']}" if m["cur"] else ""
        return f"{m['p']}{m['min']}\u2013{m['max']}/yr{cur}"
    return ""


def parse_linkedin_html(html: str) -> dict:
    """Extract job details from LinkedIn's anonymous /jobs/view/ HTML.

    LinkedIn does not expose JSON-LD on unauthenticated detail pages as of
    2026-04-10 — comp lives in a plain `<div class="compensation__salary">`.
    Returns {"comp": ...} when found, {} otherwise.
    """
    if not html:
        return {}
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # pragma: no cover - defensive only
        log.debug("[enrich] BeautifulSoup failed: %s", exc)
        return {}

    # compensation__salary is LinkedIn's stable class for the disclosed-range div.
    # Similar-jobs sidebars use .salary-info instead, so this class is precise.
    salary_div = soup.find("div", class_="compensation__salary")
    if salary_div is None:
        return {}

    raw = salary_div.get_text(strip=True)
    comp = _clean_linkedin_comp(raw)
    if not comp:
        return {}
    return {"comp": comp}


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
    except Exception:
        return {}

    text = soup.get_text(" ", strip=True)
    m = re.search(
        r"Annual Salary:\s*"
        r"(?P<p>\$|US\$|CA\$|[A-Z]{3}\s?)"
        r"(?P<min>[\d,]+)"
        r"\s*[-\u2013\u2014]\s*"
        r"(?P<p2>\$|US\$|CA\$|[A-Z]{3}\s?)?"
        r"(?P<max>[\d,]+)"
        r"(?:\s*(?P<cur>USD|CAD|GBP|EUR))?",
        text,
    )
    if not m:
        return {}
    currency_suffix = f" {m['cur']}" if m["cur"] else ""
    # Reuse the second-half prefix when present so cross-prefix ranges
    # render correctly; otherwise repeat the first half's prefix.
    max_prefix = m["p2"] or m["p"]
    return {
        "comp": f"{m['p']}{m['min']}\u2013{max_prefix}{m['max']}/yr{currency_suffix}".strip()
    }


def _parse_detail_page(html: str, url: str) -> dict:
    """Dispatch to the right detail-page parser based on URL hostname.

    LinkedIn requires its own HTML parser because it doesn't emit JSON-LD.
    Greenhouse job-boards pages also lack JSON-LD; we try JSON-LD first
    (covers any Greenhouse board that does publish structured data) and fall
    back to the text-pattern parser. Everything else uses JSON-LD only.
    """
    host = urllib.parse.urlparse(url).hostname or ""
    if "linkedin.com" in host:
        return parse_linkedin_html(html)
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
            log.debug(
                "[enrich] baseSalary present but unparseable: %r", base_salary
            )

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

    return out


# ── CSV helpers ───────────────────────────────────────────────────────────────

CANONICAL_HEADER = [
    "Status", "Company", "Product/Purpose", "Role", "Comp", "Location",
    "Fit", "GD Rating", "Source", "Date Found",
    "Date Applied", "Link", "Notes",
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
            f, fieldnames=CANONICAL_HEADER, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore",
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
                log.error("jobs.csv is missing required 'Link' column — cannot deduplicate")
                sys.exit(1)
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
                company = row[company_idx].strip() if company_idx is not None and company_idx < len(row) else ""
                role = row[role_idx].strip() if role_idx is not None and role_idx < len(row) else ""
                if company or role:
                    known_keys.add(dedup_key(company, role))
    except OSError as exc:
        log.error("Cannot read %s: %s", csv_path, exc)
        sys.exit(1)
    return known_urls, known_keys, header


def append_jobs(csv_path: Path, jobs: list[dict], header: list[str]) -> int:
    """Append new jobs to CSV. Returns count of rows written."""
    if not jobs:
        return 0

    written = 0
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=header, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
            )
            for job in jobs:
                row = {col: "" for col in header}
                row["Company"] = job.get("company", "")
                row["Product/Purpose"] = job.get("product", "(auto-scraped -- needs fill)")
                row["Role"] = job.get("role", "")
                row["Comp"] = job.get("comp", "")
                row["Location"] = job.get("location", "")
                row["Source"] = job.get("source", "")
                row["Date Found"] = job.get("date_found", date.today().isoformat())
                row["Status"] = "found"
                row["Link"] = job.get("url", "")
                writer.writerow(row)
                written += 1
    except OSError as exc:
        log.error("Cannot open %s for writing: %s", csv_path, exc)
        sys.exit(1)
    return written


# ── Role matching ─────────────────────────────────────────────────────────────

def matches_roles(title: str, roles: list[str]) -> bool:
    """True if the job title is relevant based on configured roles.

    Tier 1: exact substring match against any configured role.
    Tier 2: domain keyword + seniority keyword both present in title.
    Tier 2b: standalone domain keywords that don't need seniority (SRE, etc).
    """
    title_lower = title.lower()

    for role in roles:
        if role.lower() in title_lower:
            return True

    has_domain = any(kw in title_lower for kw in _DOMAIN_KEYWORDS)
    has_seniority = any(kw in title_lower for kw in _SENIORITY_KEYWORDS)
    if has_domain and has_seniority:
        return True

    # SRE and Platform Engineer match without seniority — they're precise enough
    # to be unambiguous IC roles. Broader terms (DevOps, Infrastructure) require
    # a seniority qualifier to avoid matching junior/intern postings.
    if any(kw in title_lower for kw in {"sre", "platform engineer"}):
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
        lower = role.lower()
        base = role
        for prefix in _SENIORITY_PREFIXES:
            if lower.startswith(prefix):
                base = role[len(prefix):]
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
    explicit = scraper_cfg(config).get("search_terms")
    if explicit:
        return explicit
    return _compress_search_terms(roles_list(config))


# ── Playwright helpers ────────────────────────────────────────────────────────

def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


@contextmanager
def _playwright_browser(config: dict):
    """Yield a Playwright Page with non-headless Chromium and realistic settings.

    Non-headless by default — avoids most bot-detection heuristics on LinkedIn,
    Indeed, and Wellfound without requiring a logged-in session.
    Set job_search.scraper.headless: true in config to run headless.
    """
    if not _has_playwright():
        raise ImportError("playwright not installed — run: pip install playwright && playwright install chromium")

    from playwright.sync_api import sync_playwright, Error as PWError

    headless = scraper_cfg(config).get("headless", False)
    ua = user_agent(config)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=headless)
        except (PWError, OSError) as exc:
            log.error("browser launch failed (run: playwright install chromium): %s", exc)
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
    """Playwright scraper for remoteok.com.

    Uses slug-encoded search URLs per term. Job rows are in a <table> with
    tr.job elements carrying data-id attributes.
    """
    roles = roles_list(config)
    terms = _search_terms(config)
    timeout_ms = timeout_seconds(config) * 1000
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    try:
        with _playwright_browser(config) as page:
            for term in terms:
                slug = term.lower().replace("/", "-").replace(" ", "-")
                url = f"https://remoteok.com/remote-{slug}-jobs"
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                except Exception as exc:
                    log.warning("[remoteok] navigation failed for %r: %s", term, exc)
                    continue

                rows = page.query_selector_all("tr.job")
                for row in rows:
                    try:
                        title_el = row.query_selector("h2[itemprop='title']")
                        company_el = row.query_selector("h3[itemprop='name']")
                        if not title_el:
                            continue
                        role = title_el.inner_text().strip()
                        company = company_el.inner_text().strip() if company_el else ""
                        if not matches_roles(role, roles):
                            continue
                        job_id = row.get_attribute("data-id") or row.get_attribute("id") or ""
                        link = f"https://remoteok.com/remote-jobs/{job_id}" if job_id else ""
                        if link in seen_urls:
                            continue
                        if link:
                            seen_urls.add(link)
                        loc_el = row.query_selector(".location")
                        location = loc_el.inner_text().strip() if loc_el else "Remote"
                        jobs.append({
                            "company": company,
                            "role": role,
                            "location": location or "Remote",
                            "url": link,
                            "source": "RemoteOK",
                            "date_found": date.today().isoformat(),
                        })
                    except Exception as exc:
                        log.debug("[remoteok] parse error on row: %s", exc)
                        continue
    except Exception as exc:
        log.warning("[remoteok] browser session error: %s", exc)

    log.info("[remoteok] %d jobs matched", len(jobs))
    return jobs


def scrape_weworkremotely(config: dict) -> list[dict]:
    """Playwright scraper for weworkremotely.com search results.

    Navigates to the search URL per term. Job items are in section.jobs > article > ul > li.
    """
    roles = roles_list(config)
    terms = _search_terms(config)
    timeout_ms = timeout_seconds(config) * 1000
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    base_url = "https://weworkremotely.com"

    try:
        with _playwright_browser(config) as page:
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                url = f"{base_url}/remote-jobs/search?term={encoded}"
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                except Exception as exc:
                    log.warning("[weworkremotely] navigation failed for %r: %s", term, exc)
                    continue

                items = page.query_selector_all("section.jobs article ul li")
                if not items:
                    items = page.query_selector_all("ul.jobs li")

                for item in items:
                    try:
                        # Skip category header items
                        if item.query_selector(".category"):
                            continue
                        title_el = (
                            item.query_selector(".title .position")
                            or item.query_selector("span.title")
                            or item.query_selector("h4")
                        )
                        company_el = (
                            item.query_selector(".company span")
                            or item.query_selector(".company")
                        )
                        link_el = item.query_selector("a[href*='/remote-jobs/']")
                        if not title_el:
                            continue
                        role = title_el.inner_text().strip()
                        company = company_el.inner_text().strip() if company_el else ""
                        if not matches_roles(role, roles):
                            continue
                        href = link_el.get_attribute("href") if link_el else ""
                        link = f"{base_url}{href}" if href and not href.startswith("http") else href
                        if link in seen_urls:
                            continue
                        if link:
                            seen_urls.add(link)
                        jobs.append({
                            "company": company,
                            "role": role,
                            "location": "Remote",
                            "url": link,
                            "source": "We Work Remotely",
                            "date_found": date.today().isoformat(),
                        })
                    except Exception as exc:
                        log.debug("[weworkremotely] parse error on item: %s", exc)
                        continue
    except Exception as exc:
        log.warning("[weworkremotely] browser session error: %s", exc)

    log.info("[weworkremotely] %d jobs matched", len(jobs))
    return jobs


def scrape_hn_who_is_hiring(config: dict) -> list[dict]:
    """Scrape the current month's HN Who's Hiring thread.

    HN is static HTML with no JS rendering — requests+BeautifulSoup is
    sufficient and avoids the Playwright startup cost.
    Comment headline format (convention): "Company | Role | Location | ..."
    """
    max_posts = scraper_cfg(config).get("hn_max_posts", 100)
    roles = roles_list(config)
    headers = {"User-Agent": user_agent(config)}
    timeout = timeout_seconds(config)
    current_month_str = date.today().strftime("%B %Y")

    try:
        index_resp = requests.get(
            "https://news.ycombinator.com/submitted?id=whoishiring",
            headers=headers,
            timeout=timeout,
        )
        index_resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("[hn_who_is_hiring] index fetch failed: %s", exc)
        return []

    index_soup = BeautifulSoup(index_resp.text, "html.parser")

    thread_url = None
    for a_tag in index_soup.find_all("a"):
        text = a_tag.get_text(strip=True)
        if "Who is hiring?" in text and current_month_str in text:
            href = a_tag.get("href", "")
            if href.startswith("item?id="):
                thread_url = f"https://news.ycombinator.com/{href}"
            elif "news.ycombinator.com" in href:
                thread_url = href
            break

    if not thread_url:
        log.warning("[hn_who_is_hiring] could not find thread for %s", current_month_str)
        return []

    try:
        thread_resp = requests.get(thread_url, headers=headers, timeout=timeout)
        thread_resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("[hn_who_is_hiring] thread fetch failed: %s", exc)
        return []

    thread_soup = BeautifulSoup(thread_resp.text, "html.parser")

    jobs = []
    comment_rows = thread_soup.find_all("tr", class_="athing comtr")

    for row in comment_rows:
        if len(jobs) >= max_posts:
            break

        ind = row.find("td", class_="ind")
        if ind:
            img = ind.find("img")
            if img and img.get("width", "0") != "0":
                continue  # nested comment

        comment_id = row.get("id", "")
        comment_div = row.find("div", class_="comment")
        if not comment_div:
            continue
        commtext = comment_div.find("div", class_="commtext")
        if not commtext:
            continue

        raw_text = commtext.get_text(separator="\n", strip=True)
        first_line = raw_text.split("\n")[0].strip()
        if not first_line:
            continue

        parts = [p.strip() for p in first_line.split("|")]
        if len(parts) < 2:
            continue

        company = parts[0]
        role = parts[1] if len(parts) > 1 else ""

        if not matches_roles(" ".join(parts), roles):
            continue

        location = "Remote"
        for part in parts[2:]:
            if re.search(r"\bremote\b", part, re.IGNORECASE):
                location = "Remote"
                break
            if len(part) < 40 and "http" not in part and "$" not in part:
                location = part
                break

        url = f"https://news.ycombinator.com/item?id={comment_id}" if comment_id else thread_url

        jobs.append({
            "company": company,
            "role": role,
            "location": location,
            "url": url,
            "source": "HN Who's Hiring",
            "date_found": date.today().isoformat(),
        })

    log.info("[hn_who_is_hiring] %d jobs matched", len(jobs))
    return jobs


def scrape_anthropic(config: dict) -> list[dict]:
    """Playwright scraper for the Anthropic careers page (Greenhouse-hosted, JS-rendered)."""
    from playwright.sync_api import TimeoutError as PWTimeout, Error as PWError

    roles = roles_list(config)
    jobs = []

    try:
        with _playwright_browser(config) as page:
            try:
                page.goto("https://www.anthropic.com/careers/jobs", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                content = page.content()
            except (PWTimeout, PWError) as exc:
                log.warning("[anthropic] page error (%s): %s", type(exc).__name__, exc)
                return []
    except Exception as exc:
        log.warning("[anthropic] browser session error: %s", exc)
        return []

    soup = BeautifulSoup(content, "html.parser")
    seen_hrefs: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if not re.search(r"greenhouse\.io/anthropic/jobs/\d+", href):
            continue
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        role_el = a_tag.select_one("[class*='jobRole'] p")
        title = role_el.get_text(strip=True) if role_el else a_tag.get_text(strip=True)
        if not title or not matches_roles(title, roles):
            continue

        loc_el = a_tag.select_one("[class*='jobLocation'] p")
        location = (loc_el.get_text(strip=True) if loc_el else "") or "Remote"

        full_url = href if href.startswith("http") else f"https://www.anthropic.com{href}"
        jobs.append({
            "company": "Anthropic",
            "role": title,
            "location": location,
            "url": full_url,
            "source": "Anthropic Careers",
            "date_found": date.today().isoformat(),
        })

    log.info("[anthropic] %d jobs matched", len(jobs))
    return jobs


def scrape_linkedin(config: dict) -> list[dict]:
    """Playwright scraper for LinkedIn public job search (no login).

    Uses non-headless Chromium to avoid bot detection. Results are limited to
    what LinkedIn serves without authentication — typically the first page of
    results per search term. f_WT=2 filters for remote, f_TPR=r86400 for last 24h.
    """
    roles = roles_list(config)
    terms = _search_terms(config)
    timeout_ms = timeout_seconds(config) * 1000
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    try:
        with _playwright_browser(config) as page:
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                url = (
                    f"https://www.linkedin.com/jobs/search/"
                    f"?keywords={encoded}&location=Canada&f_WT=2&f_TPR=r86400"
                )
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)  # LinkedIn lazy-loads cards
                except Exception as exc:
                    log.warning("[linkedin] navigation failed for %r: %s", term, exc)
                    continue

                cards = page.query_selector_all(".base-card")
                if not cards:
                    cards = page.query_selector_all(".job-search-card")

                for card in cards:
                    try:
                        title_el = (
                            card.query_selector(".base-search-card__title")
                            or card.query_selector("h3")
                        )
                        company_el = (
                            card.query_selector(".base-search-card__subtitle")
                            or card.query_selector("h4")
                        )
                        loc_el = card.query_selector(".job-search-card__location")
                        link_el = (
                            card.query_selector("a.base-card__full-link")
                            or card.query_selector("a[href*='/jobs/view/']")
                        )
                        if not title_el:
                            continue
                        role = title_el.inner_text().strip()
                        company = company_el.inner_text().strip() if company_el else ""
                        location = loc_el.inner_text().strip() if loc_el else ""
                        if not matches_roles(role, roles):
                            continue
                        href = link_el.get_attribute("href") if link_el else ""
                        # Strip tracking params from LinkedIn URLs
                        if href and "linkedin.com/jobs/view/" in href:
                            href = href.split("?")[0]
                        if href in seen_urls:
                            continue
                        if href:
                            seen_urls.add(href)
                        jobs.append({
                            "company": company,
                            "role": role,
                            "location": location or "Remote",
                            "url": href,
                            "source": "LinkedIn",
                            "date_found": date.today().isoformat(),
                        })
                    except Exception as exc:
                        log.debug("[linkedin] parse error on card: %s", exc)
                        continue
    except Exception as exc:
        log.warning("[linkedin] browser session error: %s", exc)

    log.info("[linkedin] %d jobs matched", len(jobs))
    return jobs


def scrape_indeed(config: dict) -> list[dict]:
    """Playwright scraper for ca.indeed.com public search results.

    Filters for Remote postings within the last 7 days. Skips sponsored ads.
    """
    roles = roles_list(config)
    terms = _search_terms(config)
    timeout_ms = timeout_seconds(config) * 1000
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    base_url = "https://ca.indeed.com"

    try:
        with _playwright_browser(config) as page:
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                url = f"{base_url}/jobs?q={encoded}&l=Remote&fromage=7"
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                except Exception as exc:
                    log.warning("[indeed] navigation failed for %r: %s", term, exc)
                    continue

                cards = page.query_selector_all(".job_seen_beacon")
                if not cards:
                    cards = page.query_selector_all("[data-testid='slider_item']")

                for card in cards:
                    try:
                        title_el = (
                            card.query_selector("h2.jobTitle a span")
                            or card.query_selector("h2.jobTitle")
                        )
                        company_el = (
                            card.query_selector("span.companyName")
                            or card.query_selector("[data-testid='company-name']")
                        )
                        loc_el = (
                            card.query_selector("div.companyLocation")
                            or card.query_selector("[data-testid='text-location']")
                        )
                        link_el = (
                            card.query_selector("h2.jobTitle a")
                            or card.query_selector("a[data-jk]")
                        )
                        if not title_el:
                            continue
                        role = title_el.inner_text().strip()
                        company = company_el.inner_text().strip() if company_el else ""
                        location = loc_el.inner_text().strip() if loc_el else ""
                        if not matches_roles(role, roles):
                            continue
                        href = link_el.get_attribute("href") if link_el else ""
                        if href and not href.startswith("http"):
                            href = f"{base_url}{href}"
                        # Skip sponsored/pagead results
                        if href and "pagead" in href:
                            continue
                        if href in seen_urls:
                            continue
                        if href:
                            seen_urls.add(href)
                        jobs.append({
                            "company": company,
                            "role": role,
                            "location": location or "Remote",
                            "url": href,
                            "source": "Indeed",
                            "date_found": date.today().isoformat(),
                        })
                    except Exception as exc:
                        log.debug("[indeed] parse error on card: %s", exc)
                        continue
    except Exception as exc:
        log.warning("[indeed] browser session error: %s", exc)

    log.info("[indeed] %d jobs matched", len(jobs))
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
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    try:
        with _playwright_browser(config) as page:
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                url = f"https://wellfound.com/jobs?q={encoded}&remote=true"
                try:
                    # networkidle never resolves on Wellfound's SPA — use domcontentloaded
                    # then a fixed wait for React to hydrate the job listings.
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
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
                        company_el = (
                            card.query_selector("[class*='companyName']")
                            or card.query_selector("[class*='startupName']")
                        )
                        link_el = card.query_selector("a[href*='/jobs/']")
                        if not title_el:
                            continue
                        role = title_el.inner_text().strip()
                        company = company_el.inner_text().strip() if company_el else ""
                        if not matches_roles(role, roles):
                            continue
                        href = link_el.get_attribute("href") if link_el else ""
                        if href and not href.startswith("http"):
                            href = f"https://wellfound.com{href}"
                        if href in seen_urls:
                            continue
                        if href:
                            seen_urls.add(href)
                        jobs.append({
                            "company": company,
                            "role": role,
                            "location": "Remote",
                            "url": href,
                            "source": "Wellfound",
                            "date_found": date.today().isoformat(),
                        })
                    except Exception as exc:
                        log.debug("[wellfound] parse error on card: %s", exc)
                        continue
    except Exception as exc:
        log.warning("[wellfound] browser session error: %s", exc)

    log.info("[wellfound] %d jobs matched", len(jobs))
    return jobs


def scrape_apple(config: dict) -> list[dict]:
    """Playwright scraper for Apple's Canada careers search page (React SPA).

    Searches each compressed role term. Job rows render after React hydrates;
    title links follow the pattern /en-ca/details/{id}.
    """
    roles = roles_list(config)
    terms = _search_terms(config)
    timeout_ms = timeout_seconds(config) * 1000
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    base_url = "https://jobs.apple.com"

    try:
        with _playwright_browser(config) as page:
            for term in terms:
                encoded = urllib.parse.quote_plus(term)
                url = f"{base_url}/en-ca/search?search={encoded}&sort=newest"
                try:
                    # domcontentloaded avoids networkidle timeout on React SPAs
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                except Exception as exc:
                    log.warning("[apple] navigation failed for %r: %s", term, exc)
                    continue

                rows = (
                    page.query_selector_all("tbody.table-row-container tr")
                    or page.query_selector_all("[role='row']")
                )

                for row in rows:
                    try:
                        link_el = row.query_selector("a[href*='/details/']")
                        if not link_el:
                            continue
                        role = link_el.inner_text().strip()
                        if not role or not matches_roles(role, roles):
                            continue
                        href = link_el.get_attribute("href") or ""
                        if href and not href.startswith("http"):
                            href = f"{base_url}{href}"
                        if href in seen_urls:
                            continue
                        if href:
                            seen_urls.add(href)
                        # Location is typically in a sibling <td> after the title cell
                        cells = row.query_selector_all("td")
                        location = ""
                        for cell in cells[1:]:
                            text = cell.inner_text().strip()
                            if text and "apple" not in text.lower():
                                location = text
                                break
                        jobs.append({
                            "company": "Apple",
                            "role": role,
                            "location": location or "Various",
                            "url": href,
                            "source": "Apple Careers",
                            "date_found": date.today().isoformat(),
                        })
                    except Exception as exc:
                        log.debug("[apple] parse error on row: %s", exc)
                        continue
    except Exception as exc:
        log.warning("[apple] browser session error: %s", exc)

    log.info("[apple] %d jobs matched", len(jobs))
    return jobs


# ── Orchestrator ──────────────────────────────────────────────────────────────

SCRAPERS: dict[str, Callable[[dict], list[dict]]] = {
    "remoteok": scrape_remoteok,
    "weworkremotely": scrape_weworkremotely,
    "hn_who_is_hiring": scrape_hn_who_is_hiring,
    "anthropic": scrape_anthropic,
    "linkedin": scrape_linkedin,
    "indeed": scrape_indeed,
    "wellfound": scrape_wellfound,
    "apple": scrape_apple,
}

# Sources that must run with a visible Chromium window (bot-detection hedge).
# These stay on a serial path; running 3 visible browsers concurrently is
# RAM-heavy and makes detection patterns easier to spot.
NON_HEADLESS_SOURCES = frozenset({"linkedin", "indeed", "wellfound"})


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
      Phase 2 — non-headless sources (linkedin/indeed/wellfound) run serially

    Phase 2 stays serial by design: running 3 visible Chromium windows
    concurrently is RAM-heavy and makes bot detection easier. Deduplicates by
    both URL and company+role key so the same job appearing on multiple boards
    is only kept once (first scraper wins).
    """
    source_cfg = scraper_cfg(config).get("sources", {})
    workers = int(scraper_cfg(config).get("parallel_workers", 4))
    enabled = [sid for sid in SCRAPERS if source_cfg.get(sid, False)]
    disabled = [sid for sid in SCRAPERS if not source_cfg.get(sid, False)]
    for sid in disabled:
        log.info("[%s] disabled in config, skipping", sid)

    headless_sources = [sid for sid in enabled if sid not in NON_HEADLESS_SOURCES]
    visible_sources = [sid for sid in enabled if sid in NON_HEADLESS_SOURCES]

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
            ["terminal-notifier", "-title", title, "-message", message, "-open", file_url],
            check=False,
        )
    else:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}" subtitle "{csv_path.name}"'],
            check=False,
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Scrape job boards and append to jobs.csv")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without writing to CSV")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    config_path = (
        Path(args.config) if args.config
        else Path(__file__).parent.parent / "config.yaml"
    )
    if not config_path.exists():
        log.error("config.yaml not found at %s", config_path)
        sys.exit(1)

    try:
        config = load_config(config_path)
    except yaml.YAMLError as exc:
        log.error("config.yaml is malformed: %s", exc)
        sys.exit(1)
    except OSError as exc:
        log.error("Cannot read config.yaml at %s: %s", config_path, exc)
        sys.exit(1)

    if not scraper_cfg(config).get("enabled", False):
        print("Scraper disabled. Set job_search.scraper.enabled: true in config.yaml")
        sys.exit(0)

    output_dir = resolve_output_dir(config)
    csv_path = output_dir / "jobs.csv"

    known_urls, known_keys, header = load_existing_jobs(csv_path)

    if not header:
        header = CANONICAL_HEADER
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(header)
        except OSError as exc:
            log.error("Cannot initialize %s: %s", csv_path, exc)
            sys.exit(1)
    else:
        header = _migrate_legacy_header(csv_path, header)

    log.info(
        "Loaded %d existing URLs, %d existing keys from %s",
        len(known_urls), len(known_keys), csv_path,
    )

    all_jobs, failed_sources = run_all_scrapers(config)

    # Dedup against existing DB by both URL and company+role key
    new_jobs = [
        j for j in all_jobs
        if (not j.get("url") or j["url"] not in known_urls)
        and dedup_key(j.get("company", ""), j.get("role", "")) not in known_keys
    ]

    # Drop jobs with no URL — can't dedup on future runs
    urlless = [j for j in new_jobs if not j.get("url")]
    if urlless:
        log.warning("Dropping %d jobs with no URL (cannot dedup on future runs)", len(urlless))
    new_jobs = [j for j in new_jobs if j.get("url")]

    log.info("Found %d jobs total, %d new", len(all_jobs), len(new_jobs))
    if failed_sources:
        log.warning("Failed sources: %s", ", ".join(failed_sources))

    enrich_job_details(new_jobs, config)
    enrich_company_descriptions(new_jobs)

    if args.dry_run:
        for j in new_jobs:
            print(f"  [{j['source']:22s}] {j['company']:30s} | {j['role']:45s} | {j['location']}")
            print(f"    {j['url']}")
        print(f"\n{len(new_jobs)} new jobs (dry-run, nothing written)")
        if failed_sources:
            sys.exit(1)
        return

    written = append_jobs(csv_path, new_jobs, header)
    print(f"Scraper complete: {written} new jobs appended to {csv_path}")

    if failed_sources:
        log.error("Scraper failures: %s", ", ".join(failed_sources))
        sys.exit(1)

    if written > 0:
        _notify_new_jobs(written, csv_path)


if __name__ == "__main__":  # pragma: no cover
    main()
