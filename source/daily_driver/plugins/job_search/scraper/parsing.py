"""HTML / JSON-LD parsing helpers for job-posting detail pages."""

from __future__ import annotations

import html as html_module
import json
import re
from typing import Any

from bs4 import BeautifulSoup

from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.comp import _format_comp
from daily_driver.plugins.job_search.scraper.models import _parse_k_salary

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


# A money amount as job descriptions write it: symbol-prefixed (an optional
# single space after the symbol -- "$ 150,000" appears in real postings),
# comma-grouped, optional K/M shorthand, optionally a range ("$150,000–$205,000",
# "$170,000 to $245,000", "$150K-$205K"), optionally a trailing ISO currency code.
_MONEY_RE = re.compile(
    r"(?P<p>\$|US\$|CA\$|£|€) ?"
    r"(?P<lo>\d[\d,]*[KkMm]?)"
    r"(?:\s*(?:[-–—]|to\s)\s*(?:(?P<p2>\$|US\$|CA\$|£|€) ?)?(?P<hi>\d[\d,]*[KkMm]?))?"
    r"(?:\s*(?P<cur>USD|CAD|GBP|EUR))?"
)

# Words that mark a money figure as the role's pay, searched in a short window
# BEFORE the amount. Anchoring is what keeps benefit/perk figures ("401(k)
# match up to $5,000", "$1B revenue") out of the Comp column. Bare "pay" is
# deliberately absent: it anchors one-time payments ("we will pay up to
# $20,000 for relocation") — the exact wrong-figure class this extractor must
# never emit.
_COMP_ANCHOR_RE = re.compile(
    r"\b(?:salary|compensation|base pay|pay range|pay band)\b",
    re.IGNORECASE,
)

# Words that mark a SINGLE money figure as a one-time or non-salary amount
# ("compensation includes equity valued at $500,000", "$20,000 signing
# bonus"). Applied ONLY to single-value matches, in the anchor-to-amount
# segment and a short trailing clause: real pay-transparency RANGES routinely
# say "+ bonus + equity" right after the range (corpus-measured: a
# range-inclusive disqualifier cost ~25% of true extractions), so ranges are
# exempt on evidence. Known residual: a denylist cannot catch every non-pay
# single figure ("compensation budget ... $50,000 per team") -- semantic
# parsing is out of scope for a conservative extractor; singles were 20 of
# 355 corpus hits.
_COMP_DISQUALIFIER_RE = re.compile(
    r"\b(?:bonus|equity|stock|option|relocation|severance|sign[- ]?(?:ing|on)"
    r"|401\s*\(?k\)?|reimburse\w*|stipend|allowance)\b",
    re.IGNORECASE,
)

# Sentence-ish boundaries that cap the single-value disqualifier's trailing
# look so the next sentence's benefits talk cannot veto a clean figure.
_CLAUSE_BOUNDARY_RE = re.compile(r"[.;\n!?]")

# Look-back window between the anchor word and the money figure. Wide enough
# for interposed qualifiers ("Base Salary: Senior:", "salary range for this
# role is"), narrow enough that an unrelated dollar figure a sentence later
# does not inherit the anchor.
_COMP_ANCHOR_WINDOW = 80

# Figures below the floor (after K/M expansion) are never annual salaries —
# they are bonuses, stipends, or hourly rates; figures above the ceiling are
# business numbers that drifted near a pay word ("paying customers... $100M
# ARR", observed live). Wrong comp is worse than blank, so reject both ends
# rather than guess.
_COMP_MIN_ANNUAL = 10_000
_COMP_MAX_ANNUAL = 2_000_000

# Trailing context naming a non-annual period ("$11,500 - $15,500 (Estimated
# lump sum payment...)", observed live). The figure is faithful; only the /yr
# annualization would be wrong, so emit the amount without the suffix.
_COMP_NON_ANNUAL_RE = re.compile(
    r"\b(?:lump\s+sum|per\s+month|per\s+week|/mo\b|/wk\b)",
    re.IGNORECASE,
)


def comp_from_text(text: str) -> str:
    """Extract an annual pay range from description text; "" when unsure.

    Deliberately conservative (the pipeline surfaces missing comp, but a wrong
    figure misleads): the amount must be currency-symbol-prefixed, an anchor
    word (salary/compensation/pay...) must appear within the preceding
    ``_COMP_ANCHOR_WINDOW`` characters, and the value must be annual-sized.
    The first anchored match wins. Output matches the JSON-LD formatter's
    style, e.g. "$150,000–$205,000/yr".
    """
    if not text:
        return ""
    # Cached descriptions can carry residual markup: greenhouse ships its
    # `content` entity-encoded, so the adapter's one get_text pass decodes the
    # entities and leaves literal tags — the pay range then reads
    # "<span>$137,275</span><span>&mdash;</span><span>$190,000 USD</span>"
    # and a markup-blind regex would truncate it to the low figure alone.
    # Normalize before matching: decode entities, drop tags, collapse runs of
    # whitespace so the separator sits next to its amounts.
    if "<" in text or "&" in text:
        text = html_module.unescape(text)
        # Tag-shaped tokens only: a bare "<" in prose ("roles < director")
        # must not swallow everything up to some later real closing tag --
        # that window may contain the salary itself.
        text = re.sub(r"</?[a-zA-Z][^>]*>", " ", text)
        text = re.sub(r"\s+", " ", text)
    for m in _MONEY_RE.finditer(text):
        window = text[max(0, m.start() - _COMP_ANCHOR_WINDOW) : m.start()]
        anchor = None
        for anchor in _COMP_ANCHOR_RE.finditer(window):
            pass  # keep the LAST anchor before the amount
        if anchor is None:
            continue
        lo = _parse_k_salary(m["lo"])
        if lo is None or not _COMP_MIN_ANNUAL <= lo <= _COMP_MAX_ANNUAL:
            continue
        hi = _parse_k_salary(m["hi"]) if m["hi"] else None
        if m["hi"] and (hi is None or hi < lo or hi > _COMP_MAX_ANNUAL):
            continue
        if hi is None or hi == lo:
            # Single figures carry the one-time-payment risk; check the
            # anchor-to-amount segment and the trailing clause for
            # disqualifying words. Ranges are exempt (see the constant's
            # comment for the corpus evidence).
            between = window[anchor.end() :]
            trailing = _CLAUSE_BOUNDARY_RE.split(text[m.end() : m.end() + 25])[0]
            if _COMP_DISQUALIFIER_RE.search(between + " " + trailing):
                continue
        currency_suffix = f" {m['cur']}" if m["cur"] else ""
        lookahead = text[m.end() : m.end() + 60]
        unit = "" if _COMP_NON_ANNUAL_RE.search(lookahead) else "/yr"
        if hi is not None and hi != lo:
            # a degenerate X–X range reads better as one figure
            hi_prefix = m["p2"] or m["p"]
            return f"{m['p']}{lo:,}–{hi_prefix}{hi:,}{unit}{currency_suffix}"
        return f"{m['p']}{lo:,}{unit}{currency_suffix}"
    return ""


def _parse_detail_page(html: str, url: str) -> dict:
    """Parse a fetched detail page (JSON-LD JobPosting only).

    ``url`` is kept for signature stability with callers that log per-URL.
    Greenhouse hosted pages are never fetched (bot-walled at run volume; comp
    comes from the scraped description via ``comp_from_text``), so the old
    Greenhouse text-pattern parser is gone. LinkedIn descriptions/comp come
    entirely from JobSpy's scrape-time ``linkedin_fetch_description=True``;
    no LinkedIn HTML parser runs here.
    """
    del url  # host dispatch no longer needed; single generic parser
    return parse_jsonld_jobposting(html)


def parse_jsonld_jobposting(html: str) -> dict:
    """Extract job details from JSON-LD JobPosting blocks in an HTML page.

    Returns a dict with any of: comp, posted_date. Returns an
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

    desc_html = posting.get("description", "")
    if desc_html:
        out["description_text"] = BeautifulSoup(desc_html, "html.parser").get_text(
            " ", strip=True
        )

    return out


__all__ = [
    "_fix_mojibake",
    "_find_jobposting",
    "_parse_detail_page",
    "parse_jsonld_jobposting",
]
