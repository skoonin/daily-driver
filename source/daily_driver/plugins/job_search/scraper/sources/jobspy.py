"""JobSpy source: LinkedIn + Indeed via the python-jobspy package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.comp import _COMP_CURRENCY_PREFIX, _to_int
from daily_driver.plugins.job_search.scraper.countries import (
    country_names,
    jobspy_country,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.models import RawScrapedJob
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


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
        amount = f"{lo_i:,}–{hi_i:,}"
    elif hi_i is not None:
        amount = f"{hi_i:,}"
    else:
        amount = f"{lo_i:,}"

    return f"{prefix}{amount}{suffix}"


def _comp_from_description(description: str) -> str:
    """Recover comp from description text when JobSpy returned no structured comp.

    JobSpy only regexes salary out of descriptions for the USA
    (jobspy/__init__.py); this runs the same extraction for every country. We
    don't classify currency — the raw number with its source symbol is enough to
    read alongside the location. Returns "" when no salary range is in the text.
    """
    # Lazy import: keeps this module importable without python-jobspy installed.
    from jobspy.util import extract_salary

    # enforce_annual_salary=True annualizes hourly/monthly figures so the Comp
    # column reads as a yearly amount; annualized values always carry a "yearly"
    # interval, hence the hardcoded suffix below.
    _interval, lo, hi, currency = extract_salary(
        description, enforce_annual_salary=True
    )
    if lo is None and hi is None:
        return ""
    return _format_jobspy_comp(
        {"min_amount": lo, "max_amount": hi, "currency": currency, "interval": "yearly"}
    )


def jobspy_row_to_raw(row: dict[str, Any]) -> RawScrapedJob | None:  # noqa: F821
    """Validate a JobSpy DataFrame row through RawScrapedJob (extra='ignore').

    Returns None when the role is empty (jobspy yields these for ads / non-job
    cards); pydantic would otherwise reject NonEmptyStr role.
    """
    # Local import: models.py defers imports from this package
    # (e.g. `_REMOTE_*`); a top-level import would cycle.
    from daily_driver.plugins.job_search.scraper.models import RawScrapedJob

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

    Validates through RawScrapedJob at the boundary and dumps back to
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
    comp = raw.comp_display or _comp_from_description(description)
    return {
        "company": raw.company,
        "role": raw.role,
        "location": raw.location,
        "url": raw.url,
        "source": raw.source,
        "description": description,
        "comp": comp,
        "date_found": raw.date_found.isoformat(),
    }


def scrape_jobspy(ctx: ScrapeContext, *, sites: list[str] | None = None) -> list[dict]:
    """Fetch the given site(s) (LinkedIn / Indeed) via python-jobspy in one call.

    One ``scrape_jobs`` call per (search term x country) requests every site in
    ``sites`` at once via ``site_name=[...]``. ``sites`` is the site list the
    caller decided to fetch together — the runner merges LinkedIn + Indeed into
    one list when their query knobs match and splits them otherwise (see
    ``runner._jobspy_scrape_plan``). When ``sites`` is None the enabled set is
    read from the top-level ``linkedin`` / ``indeed`` source toggles. Per-row
    Source attribution survives because ``normalize_jobspy_row`` stamps each row
    from JobSpy's own ``site`` field, so a merged call still yields rows labeled
    "linkedin" / "indeed" individually.

    Query knobs (``results_wanted_per_query`` / ``hours_old``) are read from each
    site's own toggle; the indeed-only ``country`` knob backs JobSpy's
    ``country_indeed`` fallback. python-jobspy is the implementation detail
    behind these site sources; it is not part of the user surface.

    Imported lazily so the module still loads when python-jobspy is not yet
    installed — only this scraper fails in that case, not the whole pipeline.
    JobSpy handles its own HTTP session and returns a pandas DataFrame.
    """
    from daily_driver.plugins.job_search.config import IndeedToggle, LinkedInToggle
    from daily_driver.plugins.job_search.scraper.roles import (
        _search_terms,
        matches_roles,
    )
    from daily_driver.plugins.job_search.scraper.runner import (
        countries_list,
        source_toggle,
    )

    # Lazy import: keeps --help and other scrapers functional without the package.
    try:
        from jobspy import scrape_jobs as jobspy_scrape
    except ImportError:
        log.warning(
            "[linkedin/indeed] job board library not installed — "
            "run: pip install python-jobspy"
        )
        return []

    linkedin_toggle = source_toggle(ctx.plugin, "linkedin", LinkedInToggle)
    indeed_toggle = source_toggle(ctx.plugin, "indeed", IndeedToggle)

    # Resolve the site set. An explicit `sites` arg (runner plan / tests) wins;
    # otherwise honor the per-site `enabled` flags. Glassdoor stays excluded:
    # JobSpy's path returns HTTP 400 ("location not parsed").
    if sites is None:
        enabled_sites = []
        if linkedin_toggle.enabled:
            enabled_sites.append("linkedin")
        if indeed_toggle.enabled:
            enabled_sites.append("indeed")
    else:
        enabled_sites = list(sites)
    if not enabled_sites:
        log.debug("[linkedin/indeed] no sites enabled; skipping")
        return []

    # Shared query knobs come from whichever site is in play. When the runner
    # merges both into one call it has already verified the knobs are equal, so
    # reading from either toggle is correct; prefer linkedin when present.
    knob_toggle: LinkedInToggle | IndeedToggle = (
        linkedin_toggle if "linkedin" in enabled_sites else indeed_toggle
    )
    results_wanted = knob_toggle.results_wanted_per_query
    hours_old = knob_toggle.hours_old
    # country_indeed only affects Indeed; the fallback for unknown ISO codes.
    default_country_indeed = indeed_toggle.country

    # User-facing log label is the site name(s), never the backend library.
    site_label = " + ".join(enabled_sites)

    terms = _search_terms(ctx.plugin)
    countries = countries_list(ctx.plugin)
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    # Per-site contribution tally so an enabled board that returns zero rows
    # across the whole run surfaces as a warning — the merge would otherwise hide
    # a single-site outage behind the other site's results.
    rows_per_site: dict[str, int] = {s: 0 for s in enabled_sites}

    def _call(site_list: list[str], term: str, location: str, country_indeed: str):  # type: ignore[no-untyped-def]
        """One jobspy_scrape call. Raises TypeError (version bail) to the caller;
        all other exceptions propagate so the caller can decide retry vs skip."""
        return jobspy_scrape(
            site_name=site_list,
            search_term=term,
            location=location,
            results_wanted=results_wanted,
            country_indeed=country_indeed,
            hours_old=hours_old,
            linkedin_fetch_description=True,
        )

    # Live progress unit: one (search term x country) pair. Reported at the top
    # of each iteration so a skipped/failed pair (continue below) still advances.
    # The per-board search count is announced once by the runner (on_note).
    total = len(terms) * len(countries)
    done = 0

    for term in terms:
        for country in countries:
            ctx.report(done, total)
            done += 1
            country_code = country.upper()
            country_indeed = jobspy_country(country_code, default_country_indeed)
            names = country_names(country_code)
            location_name = names[0] if names else country

            # Happy path: one merged call for all enabled sites. The installed
            # jobspy (1.1.82) has no per-site try/except, so one site RAISING
            # kills the merged DataFrame and would drop the healthy site's rows
            # for this pair too. On failure, retry each site alone so an outage
            # on one board doesn't blank the other.
            frames = []
            try:
                merged = _call(enabled_sites, term, location_name, country_indeed)
                if merged is not None and not merged.empty:
                    frames.append(merged)
            except TypeError as exc:
                # python-jobspy < 1.1.82 doesn't accept linkedin_fetch_description.
                # Bail the whole run with an actionable hint, not a per-pair warn.
                log.error(
                    "[%s] job board library is too old (%s); upgrade with "
                    "`pip install -U 'python-jobspy>=1.1.82'` or `make setup`",
                    site_label,
                    exc,
                )
                return jobs
            except Exception as exc:
                log.warning(
                    "[%s] scrape failed for %r / %s (%s); retrying each site "
                    "individually to isolate the failure",
                    site_label,
                    term,
                    country,
                    exc,
                )
                for site in enabled_sites:
                    try:
                        one = _call([site], term, location_name, country_indeed)
                    except TypeError as exc2:
                        log.error(
                            "[%s] job board library is too old (%s); upgrade with "
                            "`pip install -U 'python-jobspy>=1.1.82'` or `make setup`",
                            site,
                            exc2,
                        )
                        return jobs
                    except Exception as exc2:
                        log.warning(
                            "[%s] scrape failed for %r / %s: %s",
                            site,
                            term,
                            country,
                            exc2,
                        )
                        continue
                    if one is not None and not one.empty:
                        frames.append(one)

            if not frames:
                log.debug("[%s] no results for %r / %s", site_label, term, country)
                continue

            matched_before = len(jobs)
            for df in frames:
                for row in df.to_dict("records"):
                    normalized = normalize_jobspy_row(row)
                    if not normalized["role"] or not matches_roles(
                        normalized["role"], ctx.plugin
                    ):
                        continue
                    url = normalized["url"]
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    site = normalized["source"]
                    if site in rows_per_site:
                        rows_per_site[site] += 1
                    # Preserve description from JobSpy so detail-page enrichment
                    # is skipped (enrich_job_details short-circuits on comp, and
                    # the enricher skips rows that already have description_text).
                    normalized["description_text"] = normalized.pop("description", "")
                    jobs.append(normalized)

            log.debug(
                "[%s] %s / %s: %d matched after role filter",
                site_label,
                term,
                country,
                len(jobs) - matched_before,
            )

    # An enabled site that contributed nothing all run is the per-board outage
    # signal the merge would otherwise hide; surface it.
    for site, count in rows_per_site.items():
        if count == 0:
            log.warning(
                "[%s] returned 0 rows across all searches — possible outage or "
                "block (other enabled sites unaffected)",
                site,
            )

    log.info(
        "[%s] %d jobs matched across all terms and countries", site_label, len(jobs)
    )
    return jobs


__all__ = [
    "_jobspy_str",
    "_format_jobspy_comp",
    "jobspy_row_to_raw",
    "normalize_jobspy_row",
    "scrape_jobspy",
]
