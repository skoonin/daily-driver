"""Role matching and search-term derivation off the JobSearchPlugin model.

One-directional dependency: this module reads role config straight off the
passed ``JobSearchPlugin`` and never imports from ``runner`` — that keeps the
runner<->roles edge a single arrow and avoids an import cycle.
"""

from __future__ import annotations

import re

from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.config import JobSearchPlugin

log = get_logger(__name__)


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


def _role_pattern(role: str) -> re.Pattern[str] | None:
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


def matches_roles(
    title: str, roles: list[str], plugin: JobSearchPlugin | None = None
) -> bool:
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

    d_kws = (
        set(plugin.domain_keywords)
        if plugin and plugin.domain_keywords
        else _DEFAULT_DOMAIN_KEYWORDS
    )
    s_kws = (
        set(plugin.seniority_keywords)
        if plugin and plugin.seniority_keywords
        else _DEFAULT_SENIORITY_KEYWORDS
    )
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


def _search_terms(plugin: JobSearchPlugin) -> list[str]:
    """Return URL search query strings, compressed to base role types.

    Override by setting job_search.scraper.search_terms in config.
    """
    explicit = plugin.scraper.search_terms
    if explicit:
        return list(explicit)
    return _compress_search_terms(list(plugin.roles))
