"""HN curated jobs source: stories at news.ycombinator.com/jobs via Algolia.

Distinct from `hn_who_is_hiring`: this is the YC-funded job feed (story
posts tagged ``job``), not the monthly "Who is hiring?" thread comments.
Each posting links directly to the company's careers page.
"""

from __future__ import annotations

import re
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


_YC_BATCH_RE = re.compile(r"\s*\(YC\s+[A-Z]\d+\)\s*", re.IGNORECASE)
_HIRING_SPLIT_RE = re.compile(
    r"\s+(?:Is\s+Hiring|Hiring|Seeks)\b[:\-\s]*", re.IGNORECASE
)
_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
# Parenthesized suffix `(City)` or `(City, Region)` — strong location signal.
# Excluded: "(Remote)" handled separately above.
_PAREN_LOCATION_RE = re.compile(
    r"\(([A-Z][A-Za-z][A-Za-z\s.]*(?:,\s*[A-Z][A-Za-z.]+)*)\)\s*$"
)
# `in City, Region` with a comma — strong city+region signal that
# `in {Technology Word}` (e.g. "in Distributed Systems") cannot fake.
_IN_CITY_REGION_RE = re.compile(
    r"\bin\s+([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+)?,\s*[A-Z][A-Za-z.]+(?:,\s*[A-Z][A-Za-z.]+)?)\b"
)


def _parse_title(title: str) -> tuple[str, str, str]:
    """Split an HN /jobs title into (company, role, location).

    Location extraction is conservative — defaults to "Unknown" unless a
    strong signal is present:
      - "(Remote)" or "remote" anywhere in role → "Remote"
      - Parenthesized suffix at end like "(San Francisco)" → city
      - "in City, Region" with explicit comma → city+region
    Bare "in X" patterns (e.g. "Engineers to Build in Distributed Systems")
    are not treated as locations to avoid technology-term pollution in the
    `jobs.csv` location column.

    Title patterns observed:
      "Mux (YC W16) Is Hiring"                                       → Unknown
      "GovernGPT (YC W24) Is Hiring Engineers in Montreal"           → Unknown (no comma)
      "Coverage Cat (YC S22) Seeks Fractional Engineer ..."          → Unknown
      "Infisical (YC W23) Is Hiring Engineers (Remote)"              → Remote
      "Acme (YC W22) Is Hiring SRE in San Francisco, CA"             → "San Francisco, CA"
    """
    cleaned = _YC_BATCH_RE.sub(" ", title).strip()
    parts = _HIRING_SPLIT_RE.split(cleaned, maxsplit=1)
    company = parts[0].strip().rstrip(":-,")
    role = parts[1].strip() if len(parts) > 1 else ""

    if _REMOTE_RE.search(role):
        return company, role, "Remote"

    m = _PAREN_LOCATION_RE.search(role)
    if m:
        return company, role, m.group(1).strip().rstrip(".")

    m = _IN_CITY_REGION_RE.search(role)
    if m:
        return company, role, m.group(1).strip().rstrip(".")

    return company, role, "Unknown"


def scrape_hn_jobs(ctx: ScrapeContext) -> list[dict]:
    """Fetch curated YC job stories from HN via Algolia (`tags=job`).

    Algolia's `search_by_date` orders by recency, which matches how a user
    would browse news.ycombinator.com/jobs. ``hn_max_posts`` caps the
    number returned (same knob as `hn_who_is_hiring`).
    """
    from daily_driver.plugins.job_search.config import HackerNewsToggle
    from daily_driver.plugins.job_search.scraper.roles import matches_roles
    from daily_driver.plugins.job_search.scraper.runner import source_toggle

    max_posts = source_toggle(ctx.plugin, "hn_jobs", HackerNewsToggle).hn_max_posts
    roles = list(ctx.plugin.roles)
    session = _http_session(ctx)

    age_filter = ""
    age_days = ctx.plugin.scraper.max_age_days
    if age_days > 0:
        cutoff_epoch = int(time.time() - timedelta(days=age_days).total_seconds())
        age_filter = f"&numericFilters=created_at_i>{cutoff_epoch}"

    jobs_url = (
        "https://hn.algolia.com/api/v1/search_by_date"
        f"?tags=job&hitsPerPage={min(max(max_posts, 1), 1000)}"
        f"{age_filter}"
    )
    resp = _api_get(session, jobs_url, ctx, label="hn_jobs")
    if not resp:
        return []

    jobs: list[dict[str, Any]] = []
    for hit in resp.json().get("hits", []):
        if len(jobs) >= max_posts:
            break

        title = (hit.get("title") or "").strip()
        if not title:
            continue

        company, role, location = _parse_title(title)
        if not company:
            continue

        if not matches_roles(title, roles, ctx.plugin):
            continue

        url = (hit.get("url") or "").strip()
        if not url:
            object_id = hit.get("objectID")
            if not object_id:
                continue
            url = f"https://news.ycombinator.com/item?id={object_id}"

        jobs.append(
            {
                "company": company,
                "role": role,
                "location": location,
                "url": url,
                "source": "HN Jobs",
                "date_found": today().isoformat(),
            }
        )

    log.info("[hn_jobs] %d jobs matched", len(jobs))
    return jobs


__all__ = ["scrape_hn_jobs"]
