"""Role matching and search-term derivation off the JobSearchPlugin model.

One-directional dependency: this module reads role config straight off the
passed ``JobSearchPlugin`` and never imports from ``runner`` — that keeps the
runner<->roles edge a single arrow and avoids an import cycle.
"""

from __future__ import annotations

import re
import weakref
from dataclasses import dataclass

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


# Tier 2b standalone keywords: precise role names that match without a
# seniority prefix (the senior-only filter is delegated to config exclusions).
_TIER_2B_KEYWORDS = frozenset({"sre", "platform engineer", "site reliability engineer"})


@dataclass(frozen=True)
class RoleMatcher:
    """Precompiled role-matching state, built once per run and reused per row.

    Compiling wildcard patterns and resolving the keyword sets up front avoids
    redoing that work for every scraped title.
    """

    include_literals: tuple[str, ...]
    include_patterns: tuple[re.Pattern[str], ...]
    exclude_literals: tuple[str, ...]
    exclude_patterns: tuple[re.Pattern[str], ...]
    domain_keywords: frozenset[str]
    seniority_keywords: frozenset[str]

    @classmethod
    def from_plugin(cls, plugin: JobSearchPlugin | None) -> RoleMatcher:
        roles = list(plugin.roles) if plugin else []
        include, exclude = _split_roles(roles)
        inc_lit, inc_pat = cls._partition(include)
        exc_lit, exc_pat = cls._partition(exclude)
        d_kws = (
            frozenset(plugin.domain_keywords)
            if plugin and plugin.domain_keywords
            else frozenset(_DEFAULT_DOMAIN_KEYWORDS)
        )
        s_kws = (
            frozenset(plugin.seniority_keywords)
            if plugin and plugin.seniority_keywords
            else frozenset(_DEFAULT_SENIORITY_KEYWORDS)
        )
        return cls(inc_lit, inc_pat, exc_lit, exc_pat, d_kws, s_kws)

    @staticmethod
    def _partition(
        roles: list[str],
    ) -> tuple[tuple[str, ...], tuple[re.Pattern[str], ...]]:
        literals: list[str] = []
        patterns: list[re.Pattern[str]] = []
        for role in roles:
            pat = _role_pattern(role)
            if pat is None:
                literals.append(role.lower())
            else:
                patterns.append(pat)
        return tuple(literals), tuple(patterns)

    def matches(self, title: str) -> bool:
        """True if the title is relevant under the configured role tiers.

        Exclusions short-circuit and dominate over every other tier.
        Tier 1: literal or wildcarded include match.
        Tier 2: domain + seniority keywords both present.
        Tier 2b: standalone SRE / Platform Engineer keyword match.
        """
        title_lower = title.lower()

        if any(lit in title_lower for lit in self.exclude_literals) or any(
            pat.search(title) for pat in self.exclude_patterns
        ):
            return False

        if any(lit in title_lower for lit in self.include_literals) or any(
            pat.search(title) for pat in self.include_patterns
        ):
            return True

        has_domain = any(kw in title_lower for kw in self.domain_keywords)
        has_seniority = any(kw in title_lower for kw in self.seniority_keywords)
        if has_domain and has_seniority:
            return True

        return any(kw in title_lower for kw in _TIER_2B_KEYWORDS)


# Cache the prepared matcher per plugin instance so each run compiles role
# patterns once (the same ctx.plugin is threaded to every source). Keyed on
# id(); a weakref finalizer evicts the entry when the plugin is collected so
# the cache cannot leak or alias a recycled id within a live run.
_MATCHER_CACHE: dict[int, RoleMatcher] = {}


def _matcher_for(plugin: JobSearchPlugin | None) -> RoleMatcher:
    if plugin is None:
        return RoleMatcher.from_plugin(None)
    key = id(plugin)
    cached = _MATCHER_CACHE.get(key)
    if cached is None:
        cached = RoleMatcher.from_plugin(plugin)
        _MATCHER_CACHE[key] = cached
        weakref.finalize(plugin, _MATCHER_CACHE.pop, key, None)
    return cached


def matches_roles(title: str, plugin: JobSearchPlugin | None = None) -> bool:
    """True if the job title is relevant based on the plugin's configured roles.

    Reads roles off ``plugin`` and consults a per-plugin cached ``RoleMatcher``
    so wildcard patterns and keyword sets are compiled once per run.
    """
    return _matcher_for(plugin).matches(title)


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
