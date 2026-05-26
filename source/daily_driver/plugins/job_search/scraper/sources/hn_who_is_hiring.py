"""HN 'Who is hiring?' source: monthly thread via Algolia API."""

from __future__ import annotations

import re
from typing import Any

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

log = get_logger(__name__)


def scrape_hn_who_is_hiring(config: dict) -> list[dict]:
    """Scrape the current month's HN Who's Hiring thread via Algolia API.

    Thread discovery uses Algolia's `author_whoishiring` story search rather
    than HN's `/submitted?id=whoishiring` HTML page — HN aggressively rate-
    limits the latter (429s on repeated launchd-driven runs). Algolia is
    the single source for both the thread ID lookup and the comment fetch.
    Comment headline format (convention): "Company | Role | Location | ..."
    """
    from daily_driver.plugins.job_search.config import HackerNewsToggle
    from daily_driver.plugins.job_search.scraper.runner import (
        matches_roles,
        roles_list,
        source_toggle,
    )

    max_posts = source_toggle(config, "hn_who_is_hiring", HackerNewsToggle).hn_max_posts
    roles = roles_list(config)
    session = _http_session(config)
    current_month_str = today().strftime("%B %Y")

    stories_url = (
        "https://hn.algolia.com/api/v1/search_by_date"
        "?tags=story,author_whoishiring&hitsPerPage=12"
    )
    stories_resp = _api_get(session, stories_url, config, label="hn_who_is_hiring")
    if not stories_resp:
        return []

    thread_id: str | None = None
    expected_substrings = ("who is hiring?", current_month_str.lower())
    for hit in stories_resp.json().get("hits", []):
        title = (hit.get("title") or "").lower()
        if all(s in title for s in expected_substrings):
            object_id = hit.get("objectID")
            if object_id:
                thread_id = str(object_id)
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


__all__ = ["scrape_hn_who_is_hiring"]
