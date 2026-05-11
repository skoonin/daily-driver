"""Claude / detail-page enrichers (company, fit+notes, job details)."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from typing import TYPE_CHECKING, Any

import requests

from daily_driver.integrations import ai_provider, claude_cli
from daily_driver.integrations.ai_provider import AIInvocationError
from daily_driver.scraper.parsing import _parse_detail_page
from daily_driver.scraper.sources._http import _api_get, _http_session

if TYPE_CHECKING:
    from daily_driver.scraper.models import EnrichedJob

log = logging.getLogger(__name__)


def _enrichment_provider(config: dict | None) -> str:
    """Return the provider name configured for the enrichment task.

    Defaults to "claude" when `ai:` is omitted or malformed; the dispatch
    layer will re-validate and surface any real config errors at call time.
    """
    try:
        from daily_driver.integrations.ai_provider import _resolve_ai_config

        return _resolve_ai_config(config).enrichment.provider
    except Exception:  # noqa: BLE001
        return "claude"


def enrich_company_descriptions(
    jobs: list[dict], config: dict | None = None, *, budget: int = 0
) -> dict:
    """Populate Product/Purpose and GD Rating in-place using the Claude CLI.

    One `claude -p` call per unique company name; results cached within the run.
    Logs a warning if the `claude` CLI is not on PATH.

    Budget limits total claude calls to avoid silent stalls on slow networks —
    each call blocks for up to enrich_timeout seconds (default 30s).

    Returns a stats dict with keys: enriched, skipped_cached, failed.
    """
    from daily_driver.scraper.runner import enrich_timeout, scraper_cfg

    stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
    if _enrichment_provider(config) == "claude" and shutil.which("claude") is None:
        log.warning("[enrich] claude CLI not found on PATH, skipping product lookup")
        return stats

    cfg = scraper_cfg(config)
    if budget <= 0:
        budget = cfg.max_enrich_companies
    include_gd = cfg.enrich_gd_rating

    unique_companies = {
        job.get("company", "").strip()
        for job in jobs
        if not job.get("product") and job.get("company", "").strip()
    }
    log.info(
        "[enrich] enriching up to %d companies (%d unique)...",
        budget,
        len(unique_companies),
    )

    cache: dict[str, dict[str, str]] = {}
    calls_made = 0
    budget_warned = False

    for job in jobs:
        if job.get("product"):
            stats["skipped_cached"] += 1
            continue
        company = job.get("company", "").strip()
        if not company:
            continue
        if company not in cache:
            if calls_made >= budget:
                if not budget_warned:
                    remaining = len(
                        {
                            j.get("company", "").strip()
                            for j in jobs
                            if not j.get("product")
                            and j.get("company", "").strip()
                            and j.get("company", "").strip() not in cache
                        }
                    )
                    log.warning(
                        "[enrich] budget reached (%d), %d companies unenriched",
                        budget,
                        remaining,
                    )
                    budget_warned = True
                cache[company] = {"product": "", "gd_rating": ""}
            else:
                if include_gd:
                    prompt = (
                        f"Answer in exactly 2 lines, no preamble:\n"
                        f"Line 1: What does {company} build or do? (max 12 words)\n"
                        f"Line 2: Glassdoor rating (e.g. 4.1), or 'unknown' if unsure."
                    )
                else:
                    prompt = (
                        f"In one sentence (max 12 words), what does {company} build or do? "
                        "Answer only, no preamble."
                    )
                try:
                    stdout = ai_provider.invoke_for(
                        "enrichment",
                        prompt,
                        config=config,
                        timeout=enrich_timeout(config),
                        format_json=False,
                    )
                    lines = [ln for ln in stdout.splitlines() if ln.strip()]
                    product = lines[0] if lines else ""
                    gd_rating = ""
                    if include_gd and len(lines) >= 2:
                        raw = lines[1].strip().lower()
                        if raw == "unknown":
                            gd_rating = "unknown"
                        else:
                            try:
                                gd_rating = str(round(float(raw), 1))
                            except ValueError:
                                # Claude occasionally returns prose; extract any float in 0.0-5.9 range.
                                m = re.search(r"([0-5]\.\d)", lines[1].strip())
                                if m:
                                    gd_rating = m.group(1)
                                    log.info(
                                        "[enrich] GD rating fallback regex extracted %s for %s",
                                        gd_rating,
                                        company,
                                    )
                                else:
                                    gd_rating = ""
                    cache[company] = {"product": product, "gd_rating": gd_rating}
                except AIInvocationError as exc:
                    # Provider error messages frequently land on stdout
                    # (claude CLI auth/limit prompts; ollama HTTP bodies).
                    # Capture both tails so the cause is visible in logs.
                    stdout_tail = (exc.stdout or "").strip()[-200:]
                    stderr_tail = (exc.stderr or "").strip()[-200:]
                    log.warning(
                        "[enrich] company=%s rc=%s stdout=%r stderr=%r",
                        company,
                        exc.returncode,
                        stdout_tail,
                        stderr_tail,
                    )
                    cache[company] = {"product": "", "gd_rating": ""}
                    stats["failed"] += 1
                except (subprocess.TimeoutExpired, requests.Timeout):
                    log.warning(
                        "[enrich] %s: AI provider timed out after %ds",
                        company,
                        enrich_timeout(config),
                    )
                    cache[company] = {"product": "", "gd_rating": ""}
                    stats["failed"] += 1
                except (OSError, claude_cli.ClaudeNotFoundError) as exc:
                    log.warning("[enrich] %s lookup failed: %s", company, exc)
                    cache[company] = {"product": "", "gd_rating": ""}
                    stats["failed"] += 1
                calls_made += 1
        cached = cache.get(company, {})
        if cached.get("product"):
            job["product"] = cached["product"]
            stats["enriched"] += 1
        if cached.get("gd_rating") and not job.get("gd_rating"):
            job["gd_rating"] = cached["gd_rating"]
    return stats


def _location_summary(config: dict[str, Any]) -> str:
    from daily_driver.scraper.runner import home_city, locations_config
    from daily_driver.scraper.sources._http import COUNTRY_NAMES

    loc_cfg = locations_config(config)
    parts = [f"Based in: {home_city(config)}"]
    if loc_cfg is None:
        parts.append("Remote: yes")
        return "; ".join(parts)
    if loc_cfg.cities:
        parts.append("Preferred cities: " + ", ".join(loc_cfg.cities))
    if loc_cfg.countries:
        names = [COUNTRY_NAMES.get(c.upper(), [c])[0] for c in loc_cfg.countries]
        parts.append("Countries: " + ", ".join(names))
    if loc_cfg.remote:
        parts.append("Remote: yes")
    return "; ".join(parts)


def enrich_fit_and_notes(jobs: list[dict], config: dict, *, budget: int = 0) -> dict:
    """Populate Fit score and Notes in-place for new jobs via a single Claude CLI call per job.

    One `claude -p` call per job returns strict JSON: {"fit": <int 1-10>, "notes": "<string>"}.
    This replaces the former enrich_fit + enrich_notes pair, cutting per-job Claude spend by ~33%.

    Budget applies to the combined call count. Previously enrich_fit and enrich_notes each had
    their own budget (max_enrich_fit, max_enrich_notes). The combined budget uses max_enrich_fit
    as the limit (configurable); set it to the combined limit you want.

    Description handling: fit is scored from role/company/location alone, so description is
    optional. When description_text is absent, notes is set to "" (not confabulated). The
    skipped_no_desc counter is always 0 (fit is still scored when description is missing); the
    key is retained for shape compatibility with the former per-enricher stats.

    JSON contract expected from Claude stdout:
        {"fit": 7, "notes": "Kubernetes-heavy SRE; remote CA/US; no comp listed"}
    On description absent:
        {"fit": 7, "notes": ""}
    On insufficient info:
        {"fit": 50, "notes": ""}

    Example prompt response for "Staff SRE at Acme, Remote":
        {"fit": 8, "notes": "AWS/k8s; fully remote; no comp range listed"}

    Returns a stats dict: {"enriched": N, "skipped_budget": N, "skipped_no_desc": 0, "failed": N}.
    """
    from daily_driver.scraper.runner import (
        enrich_timeout,
        home_city,
        persona,
        scraper_cfg,
    )

    stats = {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}
    if _enrichment_provider(config) == "claude" and shutil.which("claude") is None:
        log.warning("[enrich-fit-notes] claude CLI not found on PATH, skipping")
        return stats

    cfg = scraper_cfg(config)
    if not cfg.enrich_fit or not cfg.enrich_notes:
        log.debug("[enrich-fit-notes] fit or notes disabled via config")
        return stats

    if budget <= 0:
        budget = cfg.max_enrich_fit
    loc_summary = _location_summary(config)
    role_persona = persona(config)
    hc = home_city(config)

    calls_made = 0
    for idx, job in enumerate(jobs):
        if job.get("status") == "skipped":
            continue
        if job.get("fit") and job.get("notes"):
            continue
        if calls_made >= budget:
            remaining = sum(
                1
                for j in jobs[idx:]
                if j.get("status") != "skipped"
                and not (j.get("fit") and j.get("notes"))
            )
            log.warning(
                "[enrich-fit-notes] budget reached (%d), skipping %d jobs",
                budget,
                remaining,
            )
            stats["skipped_budget"] += remaining
            break

        role = job.get("role", "unknown")
        company = job.get("company", "unknown")
        location = job.get("location", "unknown")
        product = job.get("product", "")
        desc = job.get("description_text", "")

        desc_section = ""
        if desc:
            words = desc.split()
            if len(words) > 500:
                desc = " ".join(words[:500]) + " ..."
            desc_section = f"\nDescription: {desc}"

        prompt = (
            f"You are evaluating a job for a {role_persona}.\n"
            f"Location preferences: {loc_summary}\n"
            f"Job: {role} at {company}, {location}\n"
        )
        if product:
            prompt += f"Company: {product}\n"
        prompt += (
            f"{desc_section}\n\n"
            "Reply with ONLY valid JSON on a single line, exactly this shape:\n"
            '{"fit": <integer 1-10>, "notes": "<one line, max 25 words>"}\n\n'
            "fit: rate 1-10 how well this role fits the candidate based on role/company/location. "
            "If you truly lack enough information to assess, return 5.\n"
            "notes: one-line summary including key tech stack, remote policy "
            f"(e.g. 'Remote US-only', 'Hybrid {hc}', 'Remote {hc} OK'), red flags. "
            "If description is absent, return notes as empty string. "
            "Do not guess or hallucinate notes when you have no description.\n"
            "Do not include any preamble, explanation, or markdown -- only the JSON object."
        )

        try:
            stdout = ai_provider.invoke_for(
                "enrichment",
                prompt,
                config=config,
                timeout=enrich_timeout(config),
                format_json=True,
            )
            raw = stdout.strip()
            try:
                parsed = json.loads(raw)
                fit_val = parsed.get("fit")
                notes_val = parsed.get("notes", "")
                if isinstance(fit_val, (int, float)):
                    score = min(int(fit_val), 10)
                    if not job.get("fit"):
                        job["fit"] = f"{score}/10"
                    if not job.get("notes") and isinstance(notes_val, str):
                        job["notes"] = notes_val
                    stats["enriched"] += 1
                else:
                    log.warning(
                        "[enrich-fit-notes] company=%s role=%s: JSON missing valid fit field: %r",
                        company,
                        role,
                        raw[:200],
                    )
                    stats["failed"] += 1
            except json.JSONDecodeError:
                log.warning(
                    "[enrich-fit-notes] company=%s role=%s: non-JSON response: %r",
                    company,
                    role,
                    raw[:200],
                )
                stats["failed"] += 1
        except AIInvocationError as exc:
            stdout_tail = (exc.stdout or "").strip()[-200:]
            stderr_tail = (exc.stderr or "").strip()[-200:]
            log.warning(
                "[enrich-fit-notes] company=%s role=%s rc=%s stdout=%r stderr=%r",
                company,
                role,
                exc.returncode,
                stdout_tail,
                stderr_tail,
            )
            stats["failed"] += 1
        except (subprocess.TimeoutExpired, requests.Timeout):
            log.warning(
                "[enrich-fit-notes] %s: AI provider timed out after %ds",
                company,
                enrich_timeout(config),
            )
            stats["failed"] += 1
        except (OSError, claude_cli.ClaudeNotFoundError) as exc:
            log.warning("[enrich-fit-notes] %s failed: %s", company, exc)
            stats["failed"] += 1
        calls_made += 1
    return stats


def enrich_job_details(jobs: list[dict], config: dict) -> None:
    """Fetch each job's detail page and populate comp/posted_date in place.

    Caches by URL within the run so jobs that share a detail URL only generate
    one HTTP request. Skips jobs that already have `comp` set so listing-card
    sources that already provide salary aren't clobbered. Network/parse errors
    are swallowed — missing data is the expected outcome for boards that don't
    expose JSON-LD, not an error worth aborting the run for.
    """
    from daily_driver.scraper.runner import scraper_cfg

    cache: dict[str, dict] = {}
    cfg = scraper_cfg(config)
    delay = cfg.detail_delay_seconds

    # Hosts whose detail pages we deliberately skip:
    # - linkedin.com: anonymous detail pages don't emit JSON-LD; JobSpy's
    #   linkedin_fetch_description already populates description.
    # - news.ycombinator.com: aggressive 429 rate-limiting on /item?id=*.
    #   Title-derived data from hn_who_is_hiring / hn_jobs is sufficient.
    #   A future Algolia /api/v1/items/{id} integration could replace this.
    # - *.indeed.com: bot-walls bare requests with 403; JobSpy already
    #   populates description for Indeed listings.
    hn_skipped_logged = False
    indeed_skipped_logged = False

    session: requests.Session | None = None
    fetched_count = 0
    enriched_count = 0
    for job in jobs:
        if job.get("comp"):
            continue
        if job.get("status") == "skipped":
            continue
        url = (job.get("url") or "").strip()
        if not url:
            continue
        if "linkedin.com" in url:
            continue
        if "news.ycombinator.com" in url:
            if not hn_skipped_logged:
                log.info(
                    "[detail] skipping HN detail enrichment "
                    "(rate-limited; title-derived data only)"
                )
                hn_skipped_logged = True
            continue
        if "indeed.com" in url:
            if not indeed_skipped_logged:
                log.info(
                    "[detail] skipping Indeed detail enrichment "
                    "(JobSpy already populates description)"
                )
                indeed_skipped_logged = True
            continue

        if url in cache:
            details = cache[url]
        else:
            if fetched_count > 0 and delay > 0:
                time.sleep(delay)
            fetched_count += 1
            if session is None:
                session = _http_session(config)
            resp = _api_get(session, url, config, label="detail")
            if resp is None:
                details = {}
            else:
                try:
                    details = _parse_detail_page(resp.text, url)
                except ValueError as exc:
                    # Malformed page data (bad JSON-LD, unexpected shape) is
                    # non-fatal: missing comp just means the CSV stays blank.
                    # Programmer errors (AttributeError, TypeError) are
                    # deliberately NOT caught — those signal real regressions
                    # in `_parse_detail_page` and must remain visible.
                    log.warning("[detail] %s: parse failed: %s", url, exc)
                    details = {}
            cache[url] = details

        if details.get("comp"):
            job["comp"] = details["comp"]
            enriched_count += 1
        if details.get("posted_date") and not job.get("posted_date"):
            job["posted_date"] = details["posted_date"]
        if details.get("description_text") and not job.get("description_text"):
            job["description_text"] = details["description_text"]

    log.info(
        "[detail] fetched %d pages, enriched %d of %d jobs",
        fetched_count,
        enriched_count,
        len(jobs),
    )


def enrich_company_descriptions_typed(
    jobs: list[EnrichedJob],  # noqa: F821
    config: dict[str, Any] | None = None,
    *,
    budget: int = 0,
) -> tuple[list[EnrichedJob], dict[str, int]]:  # noqa: F821
    """Typed wrapper around ``enrich_company_descriptions`` (K8).

    Round-trips through a working-dict list because the legacy body mutates
    in place; returns a fresh list of frozen EnrichedJob instances built via
    ``model_copy(update=...)``.
    """
    from daily_driver.scraper.csv_io import (
        _dict_to_enriched_updates,
        _enriched_to_dict,
    )

    working = [_enriched_to_dict(j) for j in jobs]
    stats = enrich_company_descriptions(working, config, budget=budget)
    out = [
        j.model_copy(update=_dict_to_enriched_updates(d))
        for j, d in zip(jobs, working, strict=True)
    ]
    return out, stats


def enrich_fit_and_notes_typed(
    jobs: list[EnrichedJob],  # noqa: F821
    config: dict[str, Any],
    *,
    budget: int = 0,
) -> tuple[list[EnrichedJob], dict[str, int]]:  # noqa: F821
    """Typed wrapper around ``enrich_fit_and_notes`` (K8)."""
    from daily_driver.scraper.csv_io import (
        _dict_to_enriched_updates,
        _enriched_to_dict,
    )

    working = [_enriched_to_dict(j) for j in jobs]
    stats = enrich_fit_and_notes(working, config, budget=budget)
    out = [
        j.model_copy(update=_dict_to_enriched_updates(d))
        for j, d in zip(jobs, working, strict=True)
    ]
    return out, stats


def enrich_job_details_typed(
    jobs: list[EnrichedJob],  # noqa: F821
    config: dict[str, Any],
) -> list[EnrichedJob]:  # noqa: F821
    """Typed wrapper around ``enrich_job_details`` (K8).

    The legacy ``enrich_job_details`` returns ``None`` and mutates in place;
    this wrapper produces a fresh list with each job's description_text /
    posted_date / comp populated where the detail fetch supplied them.
    """
    import datetime as dt

    from daily_driver.scraper.csv_io import (
        _dict_to_enriched_updates,
        _enriched_to_dict,
    )

    working = [_enriched_to_dict(j) for j in jobs]
    enrich_job_details(working, config)
    out: list[Any] = []
    for j, d in zip(jobs, working, strict=True):
        updates = _dict_to_enriched_updates(d)
        # Detail enricher may also write posted_date as ISO string.
        if d.get("posted_date") and isinstance(d["posted_date"], str):
            try:
                updates["posted_date"] = dt.date.fromisoformat(d["posted_date"])
            except ValueError:
                pass
        # Comp from JSON-LD comes back via "comp" string; already handled.
        out.append(j.model_copy(update=updates))
    return out


__all__ = [
    "_location_summary",
    "enrich_company_descriptions",
    "enrich_company_descriptions_typed",
    "enrich_fit_and_notes",
    "enrich_fit_and_notes_typed",
    "enrich_job_details",
    "enrich_job_details_typed",
]
