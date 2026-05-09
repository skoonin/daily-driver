"""JobSpy source: LinkedIn + Indeed + Google via the python-jobspy package."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from daily_driver.core.clock import today
from daily_driver.scraper.comp import _COMP_CURRENCY_PREFIX, _to_int
from daily_driver.scraper.sources._http import COUNTRY_NAMES

if TYPE_CHECKING:
    from daily_driver.scraper.models import RawScrapedJob

log = logging.getLogger(__name__)


# Country codes JobSpy's indeed engine accepts. Maps from ISO codes used in
# config to the string values JobSpy's country_indeed parameter expects.
_JOBSPY_COUNTRY_MAP: dict[str, str] = {
    "US": "USA",
    "CA": "Canada",
    "GB": "UK",
    "IE": "Ireland",
    "AU": "Australia",
    "ZA": "South Africa",
}

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


def jobspy_row_to_raw(row: dict[str, Any]) -> RawScrapedJob | None:  # noqa: F821
    """Validate a JobSpy DataFrame row through RawScrapedJob (Q15: extra='ignore').

    Returns None when the role is empty (jobspy yields these for ads / non-job
    cards); pydantic would otherwise reject NonEmptyStr role.
    """
    # Local import: models.py uses deferred imports from this module
    # (`_parse_comp`, `_REMOTE_*`); top-level import would cycle until K4/K9.
    from daily_driver.scraper.models import RawScrapedJob

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

    Validates through RawScrapedJob at the boundary (Q15) and dumps back to
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
    return {
        "company": raw.company,
        "role": raw.role,
        "location": raw.location,
        "url": raw.url,
        "source": raw.source,
        "description": description,
        "comp": raw.comp_display,
        "date_found": raw.date_found.isoformat(),
    }


def scrape_jobspy(config: dict) -> list[dict]:
    """Headless multi-source scraper via JobSpy (LinkedIn, Indeed, Glassdoor, Google).

    Imported lazily so the module still loads when python-jobspy is not yet
    installed — only this scraper fails in that case, not the whole pipeline.
    JobSpy handles its own HTTP session and returns a pandas DataFrame.
    """
    from daily_driver.scraper.runner import (
        _search_terms,
        countries_list,
        matches_roles,
        roles_list,
        scraper_cfg,
    )

    # Lazy import: keeps --help and other scrapers functional without the package.
    try:
        from jobspy import scrape_jobs as jobspy_scrape
    except ImportError:
        log.warning(
            "[jobspy] python-jobspy not installed — run: pip install python-jobspy"
        )
        return []

    cfg = scraper_cfg(config)
    jobspy_cfg = cfg.jobs
    results_wanted = jobspy_cfg.results_wanted_per_query
    hours_old = jobspy_cfg.hours_old
    default_country_indeed = jobspy_cfg.country_indeed

    roles = roles_list(config)
    terms = _search_terms(config)
    countries = countries_list(config)
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    for term in terms:
        for country in countries:
            country_code = country.upper()
            country_indeed = _JOBSPY_COUNTRY_MAP.get(
                country_code, default_country_indeed
            )
            location_name = COUNTRY_NAMES.get(country_code, [country])[0]
            # Glassdoor disabled: JobSpy's Glassdoor path returns HTTP 400
            # ("location not parsed") on every request and retries for minutes.
            sites = ["linkedin", "indeed", "google"]
            try:
                df = jobspy_scrape(
                    site_name=sites,
                    search_term=term,
                    location=location_name,
                    results_wanted=results_wanted,
                    country_indeed=country_indeed,
                    hours_old=hours_old,
                    linkedin_fetch_description=True,
                )
            except TypeError as exc:
                # python-jobspy < 1.1.82 doesn't accept linkedin_fetch_description.
                # Bail out of the entire run with an actionable hint rather than
                # emitting a generic warning per (term, country) iteration.
                log.error(
                    "[jobspy] python-jobspy is too old (%s); upgrade with "
                    "`pip install -U 'python-jobspy>=1.1.82'` or `make setup`",
                    exc,
                )
                return jobs
            except Exception as exc:
                log.warning(
                    "[jobspy] scrape failed for %r / %s: %s", term, country, exc
                )
                continue

            if df is None or df.empty:
                log.debug("[jobspy] no results for %r / %s", term, country)
                continue

            records = df.to_dict("records")
            for row in records:
                normalized = normalize_jobspy_row(row)
                if not normalized["role"] or not matches_roles(
                    normalized["role"], roles, config
                ):
                    continue
                url = normalized["url"]
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                # Preserve description from JobSpy so detail-page enrichment
                # is skipped (enrich_job_details already short-circuits on comp,
                # and the enricher skips rows that already have description_text).
                normalized["description_text"] = normalized.pop("description", "")
                jobs.append(normalized)

            log.debug(
                "[jobspy] %s / %s: %d records, %d matched after role filter",
                term,
                country,
                len(records),
                sum(1 for j in jobs if j.get("date_found") == today().isoformat()),
            )

    log.info("[jobspy] %d jobs matched across all terms and countries", len(jobs))
    return jobs


__all__ = [
    "_jobspy_str",
    "_format_jobspy_comp",
    "jobspy_row_to_raw",
    "normalize_jobspy_row",
    "scrape_jobspy",
]
