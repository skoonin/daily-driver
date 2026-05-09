"""HN 'Who is hiring?' source: monthly thread via Algolia API."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from daily_driver.core.clock import today
from daily_driver.scraper.sources._http import _api_get, _http_session

log = logging.getLogger(__name__)


def scrape_hn_who_is_hiring(config: dict) -> list[dict]:
    """Scrape the current month's HN Who's Hiring thread via Algolia API.

    Index-page fetch resolves the thread ID from HN's HTML (unchanged).
    Comment retrieval uses Algolia's public search API instead of parsing
    HN's HTML comment tree, which breaks on markup changes.
    Comment headline format (convention): "Company | Role | Location | ..."
    """
    from daily_driver.scraper._impl import matches_roles, roles_list, scraper_cfg

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


__all__ = ["scrape_hn_who_is_hiring"]
