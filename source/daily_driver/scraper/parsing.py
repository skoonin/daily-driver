"""HTML / JSON-LD parsing helpers for job-posting detail pages."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

from daily_driver.core.logging import get_logger
from daily_driver.scraper.comp import _format_comp
from daily_driver.scraper.models import _parse_k_salary

log = get_logger(__name__)


def _fix_mojibake(text: str) -> str:
    """Reverse the common UTF-8-as-latin-1 mis-decoding (em-dash → 'â€"' etc.).

    Returns text unchanged when the round-trip would lose data — pure ASCII
    and legitimate latin-1 characters (e.g. 'é') survive intact.
    """
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


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
        r"\s*[-–—]\s*"
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
                f"{m['p']}{lo:,}–{max_prefix}{hi:,}/yr{currency_suffix}".strip()
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


__all__ = [
    "_fix_mojibake",
    "_find_jobposting",
    "parse_greenhouse_html",
    "_parse_detail_page",
    "parse_jsonld_jobposting",
]
