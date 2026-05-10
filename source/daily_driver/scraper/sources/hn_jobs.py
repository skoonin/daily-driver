"""HN curated jobs source: stories at news.ycombinator.com/jobs via Algolia.

Distinct from `hn_who_is_hiring`: this is the YC-funded job feed (story
posts tagged ``job``), not the monthly "Who is hiring?" thread comments.
Each posting links directly to the company's careers page.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta
from typing import Any

from daily_driver.core.clock import today
from daily_driver.scraper.sources._http import _api_get, _http_session

log = logging.getLogger(__name__)


_YC_BATCH_RE = re.compile(r"\s*\(YC\s+[A-Z]\d+\)\s*", re.IGNORECASE)
_HIRING_SPLIT_RE = re.compile(
    r"\s+(?:Is\s+Hiring|Hiring|Seeks|Wants)\b[:\-\s]*", re.IGNORECASE
)
_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_LOCATION_IN_RE = re.compile(r"\bin\s+([A-Z][A-Za-z\s,]+)$")


def _parse_title(title: str) -> tuple[str, str, str]:
    """Split an HN /jobs title into (company, role, location).

    Title patterns observed:
      "Mux (YC W16) Is Hiring"
      "GovernGPT (YC W24) Is Hiring Engineers to Build ... in Montreal"
      "Coverage Cat (YC S22) Seeks Fractional Engineer to ..."
      "Infisical (YC W23) Is Hiring Full Stack Software Engineers (Remote)"
      "Stardex Is Hiring a Founding Customer Success Lead"      (no YC tag)
    """
    cleaned = _YC_BATCH_RE.sub(" ", title).strip()
    parts = _HIRING_SPLIT_RE.split(cleaned, maxsplit=1)
    company = parts[0].strip().rstrip(":-,")
    role = parts[1].strip() if len(parts) > 1 else ""

    location = "Remote"
    if _REMOTE_RE.search(role):
        location = "Remote"
    else:
        m = _LOCATION_IN_RE.search(role)
        if m:
            location = m.group(1).strip().rstrip(".")

    return company, role, location


def scrape_hn_jobs(config: dict) -> list[dict]:
    """Fetch curated YC job stories from HN via Algolia (`tags=job`).

    Algolia's `search_by_date` orders by recency, which matches how a user
    would browse news.ycombinator.com/jobs. ``hn_max_posts`` caps the
    number returned (same knob as `hn_who_is_hiring`).
    """
    from daily_driver.scraper.runner import (
        matches_roles,
        max_age_days,
        roles_list,
        scraper_cfg,
    )

    max_posts = scraper_cfg(config).hn_max_posts
    roles = roles_list(config)
    session = _http_session(config)

    age_filter = ""
    age_days = max_age_days(config)
    if age_days > 0:
        cutoff_epoch = int(time.time() - timedelta(days=age_days).total_seconds())
        age_filter = f"&numericFilters=created_at_i>{cutoff_epoch}"

    jobs_url = (
        "https://hn.algolia.com/api/v1/search_by_date"
        f"?tags=job&hitsPerPage={min(max(max_posts, 1), 1000)}"
        f"{age_filter}"
    )
    resp = _api_get(session, jobs_url, config, label="hn_jobs")
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

        if not matches_roles(title, roles, config):
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
