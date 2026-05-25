"""Claude / detail-page enrichers (company, fit+notes, job details)."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import sys
import time
from typing import TYPE_CHECKING, Any

from daily_driver.core.logging import get_logger
from daily_driver.integrations import ai_provider, claude_cli
from daily_driver.integrations.ai_provider import AIInvocationError, AITimeoutError
from daily_driver.plugins.job_search.scraper.parsing import _parse_detail_page
from daily_driver.plugins.job_search.scraper.sources._http import (
    Session,
    _api_get,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob


def _ollama_pool_size(config: dict | None) -> int:
    """Worker count for Ollama-backed enrichment; 1 forces serial."""
    ai_cfg = ai_provider.resolve_ai_config(config)
    if ai_cfg.enrichment.provider != "ollama":
        return 1
    return max(1, ai_cfg.ollama.max_parallel)


def _enrich_tag(prefix: str) -> str:
    """Return [prefix] in main thread, [prefix wN] in a pool worker."""
    import threading

    name = threading.current_thread().name
    if name == "MainThread":
        return f"[{prefix}]"
    suffix = name.rsplit("_", 1)
    if len(suffix) == 2 and suffix[1].isdigit():
        return f"[{prefix} w{suffix[1]}]"
    return f"[{prefix}]"


def _install_interrupt_notifier(futures: dict, timeout_s: int, item: str) -> Any:
    """Install a SIGINT handler that prints a user-voice ack on first Ctrl-C.

    Second Ctrl-C restores the OS default handler and re-sends SIGINT so the
    process exits the way it would have without us. Returns the previous
    handler so the caller can restore it in a finally clause.

    `item` is the user-vocabulary noun ("companies" or "jobs"); `futures`
    is the live mapping so the message can name how many are in progress.
    """
    interrupt_count = [0]
    previous = signal.getsignal(signal.SIGINT)

    def handler(_signum: int, _frame: Any) -> None:
        interrupt_count[0] += 1
        if interrupt_count[0] == 1:
            in_flight = sum(1 for f in futures if not f.done())
            print(
                f"\nStopping — waiting for {in_flight} {item} still being "
                f"enriched (up to {timeout_s}s each). Press Ctrl-C again "
                "to quit now and lose what's in progress.",
                file=sys.stderr,
                flush=True,
            )
            raise KeyboardInterrupt
        # Second press: hand control back to whatever handler the parent had
        # before we installed ours, then re-raise the signal. Falls back to
        # SIG_DFL if `previous` isn't installable (e.g. a non-callable token).
        try:
            signal.signal(signal.SIGINT, previous)
        except (TypeError, ValueError):
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGINT)

    signal.signal(signal.SIGINT, handler)
    return previous


log = get_logger(__name__)


def _fetch_company_info(
    company: str,
    config: dict | None,
    include_gd: bool,
    timeout: int,
) -> tuple[str, str, bool]:
    """Fetch product/GD-rating for one company. Returns (product, gd_rating, failed).

    Worker function: catches all expected exceptions and returns failed=True
    instead of propagating, so it is safe to call from a thread pool.
    """
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
            "enrichment", prompt, config=config, timeout=timeout, format_json=False
        )
    except AITimeoutError as exc:
        log.warning(
            "%s %s: AI provider timed out after %ds",
            _enrich_tag("enrich"),
            company,
            exc.timeout_seconds or timeout,
        )
        return "", "", True
    except AIInvocationError as exc:
        stdout_tail = (exc.stdout or "").strip()[-200:]
        stderr_tail = (exc.stderr or "").strip()[-200:]
        log.warning(
            "%s company=%s rc=%s stdout=%r stderr=%r",
            _enrich_tag("enrich"),
            company,
            exc.returncode,
            stdout_tail,
            stderr_tail,
        )
        return "", "", True
    except (FileNotFoundError, PermissionError, claude_cli.ClaudeNotFoundError) as exc:
        log.warning("%s %s lookup failed: %s", _enrich_tag("enrich"), company, exc)
        return "", "", True

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
                m = re.search(r"([0-5]\.\d)", lines[1].strip())
                if m:
                    gd_rating = m.group(1)
                    log.info(
                        "%s GD rating fallback regex extracted %s for %s",
                        _enrich_tag("enrich"),
                        gd_rating,
                        company,
                    )
    return product, gd_rating, False


def _enrich_company_descriptions_parallel(
    jobs: list[dict],
    config: dict,
    *,
    budget: int,
    include_gd: bool,
    pool_size: int,
    unique_companies: set[str],
    stats: dict,
    timeout: int,
) -> dict:
    """Parallel ollama path for enrich_company_descriptions."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    companies = list(unique_companies)[:budget]
    if len(unique_companies) > budget:
        log.warning(
            "[enrich] budget reached (%d), %d companies unenriched",
            budget,
            len(unique_companies) - budget,
        )

    cache: dict[str, dict[str, str]] = {}

    def _stitch() -> None:
        for job in jobs:
            if job.get("product"):
                stats["skipped_cached"] += 1
                continue
            company = job.get("company", "").strip()
            if not company:
                continue
            cached = cache.get(company, {})
            if cached.get("product"):
                job["product"] = cached["product"]
                stats["enriched"] += 1
            if cached.get("gd_rating") and not job.get("gd_rating"):
                job["gd_rating"] = cached["gd_rating"]

    pool = ThreadPoolExecutor(max_workers=pool_size)
    # Populate `futures` incrementally so the SIGINT handler always sees a
    # mapping that reflects what's actually been submitted. A bulk
    # `futures.update({comprehension})` replaces the dict atomically, leaving
    # a window where the handler would read {} and report "0 in progress".
    futures: dict = {}
    previous_handler = _install_interrupt_notifier(futures, timeout, "companies")
    for c in companies:
        futures[pool.submit(_fetch_company_info, c, config, include_gd, timeout)] = c
    try:
        for fut in as_completed(futures):
            company = futures[fut]
            product, gd_rating, failed = fut.result()
            cache[company] = {"product": product, "gd_rating": gd_rating}
            if failed:
                stats["failed"] += 1
        pool.shutdown(wait=True)
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        for fut in futures:
            if fut.done() and not fut.cancelled():
                company = futures[fut]
                if company not in cache:
                    product, gd_rating, failed = fut.result()
                    cache[company] = {"product": product, "gd_rating": gd_rating}
                    if failed:
                        stats["failed"] += 1
        _stitch()
        raise
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    _stitch()
    return stats


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
    from daily_driver.plugins.job_search.scraper.runner import (
        enrich_timeout,
        enrichment_cfg,
    )

    stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
    if (
        ai_provider.resolve_ai_config(config).enrichment.provider == "claude"
        and shutil.which("claude") is None
    ):
        log.warning("[enrich] claude CLI not found on PATH, skipping product lookup")
        return stats

    cfg = enrichment_cfg(config)
    if budget <= 0:
        budget = cfg.max_enrich_companies
    include_gd = cfg.enrich_gd_rating

    unique_companies = {
        job.get("company", "").strip()
        for job in jobs
        if not job.get("product") and job.get("company", "").strip()
    }
    pool_size = _ollama_pool_size(config)
    log.info(
        "[enrich] enriching up to %d companies (%d unique%s)...",
        budget,
        len(unique_companies),
        f", parallel={pool_size}" if pool_size > 1 else "",
    )

    if pool_size > 1 and config is not None:
        return _enrich_company_descriptions_parallel(
            jobs,
            config,
            budget=budget,
            include_gd=include_gd,
            pool_size=pool_size,
            unique_companies=unique_companies,
            stats=stats,
            timeout=enrich_timeout(config),
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
                product, gd_rating, failed = _fetch_company_info(
                    company, config, include_gd, enrich_timeout(config)
                )
                cache[company] = {"product": product, "gd_rating": gd_rating}
                if failed:
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
    from daily_driver.plugins.job_search.scraper.runner import (
        home_city,
        locations_config,
    )
    from daily_driver.plugins.job_search.scraper.sources._http import COUNTRY_NAMES

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


def _build_fit_notes_prompt(
    job: dict, role_persona: str, loc_summary: str, hc: str
) -> str:
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
    return prompt


def _fetch_fit_notes_for_job(
    job: dict,
    role_persona: str,
    loc_summary: str,
    hc: str,
    config: dict | None,
    timeout: int,
) -> tuple[str, str, bool]:
    """Worker: fetch fit/notes for one job. Returns (fit_str, notes_str, failed)."""
    company = job.get("company", "unknown")
    role = job.get("role", "unknown")
    prompt = _build_fit_notes_prompt(job, role_persona, loc_summary, hc)
    log.debug(
        "%s company=%s role=%s prompt=%r",
        _enrich_tag("enrich-fit-notes"),
        company,
        role,
        prompt,
    )

    try:
        stdout = ai_provider.invoke_for(
            "enrichment", prompt, config=config, timeout=timeout, format_json=True
        )
    except AITimeoutError as exc:
        log.warning(
            "%s %s: AI provider timed out after %ds",
            _enrich_tag("enrich-fit-notes"),
            company,
            exc.timeout_seconds or timeout,
        )
        return "", "", True
    except AIInvocationError as exc:
        stdout_tail = (exc.stdout or "").strip()[-200:]
        stderr_tail = (exc.stderr or "").strip()[-200:]
        log.warning(
            "%s company=%s role=%s rc=%s stdout=%r stderr=%r",
            _enrich_tag("enrich-fit-notes"),
            company,
            role,
            exc.returncode,
            stdout_tail,
            stderr_tail,
        )
        return "", "", True
    except (FileNotFoundError, PermissionError, claude_cli.ClaudeNotFoundError) as exc:
        log.warning("%s %s failed: %s", _enrich_tag("enrich-fit-notes"), company, exc)
        return "", "", True

    raw = stdout.strip()
    log.debug(
        "%s company=%s role=%s raw_response=%r",
        _enrich_tag("enrich-fit-notes"),
        company,
        role,
        raw,
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning(
            "%s company=%s role=%s: non-JSON response: %r",
            _enrich_tag("enrich-fit-notes"),
            company,
            role,
            raw[:200],
        )
        return "", "", True

    fit_val = parsed.get("fit")
    notes_val = parsed.get("notes", "")
    if not isinstance(fit_val, (int, float)):
        log.warning(
            "%s company=%s role=%s: JSON missing valid fit field: %r",
            _enrich_tag("enrich-fit-notes"),
            company,
            role,
            raw[:200],
        )
        return "", "", True

    score = min(int(fit_val), 10)
    notes_str = notes_val if isinstance(notes_val, str) else ""
    log.debug(
        "%s company=%s role=%s parsed fit=%d/10 notes=%r",
        _enrich_tag("enrich-fit-notes"),
        company,
        role,
        score,
        notes_str,
    )
    return f"{score}/10", notes_str, False


def _enrich_fit_and_notes_parallel(
    jobs: list[dict],
    config: dict,
    *,
    budget: int,
    pool_size: int,
    role_persona: str,
    loc_summary: str,
    hc: str,
    stats: dict,
    timeout: int,
) -> dict:
    """Parallel ollama path for enrich_fit_and_notes."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    eligible = [
        j
        for j in jobs
        if j.get("status") != "skipped" and not (j.get("fit") and j.get("notes"))
    ]
    targets = eligible[:budget]
    if len(eligible) > budget:
        log.warning(
            "[enrich-fit-notes] budget reached (%d), skipping %d jobs",
            budget,
            len(eligible) - budget,
        )
        stats["skipped_budget"] += len(eligible) - budget

    def _apply(job: dict, fit_str: str, notes_str: str, failed: bool) -> None:
        company = job.get("company", "unknown")
        pre_fit = job.get("fit", "")
        pre_notes = job.get("notes", "")
        if failed:
            stats["failed"] += 1
            log.debug(
                "[enrich-fit-notes] %s: call failed, no write " "(pre fit=%r notes=%r)",
                company,
                pre_fit,
                pre_notes,
            )
            return
        wrote_fit = not job.get("fit") and bool(fit_str)
        wrote_notes = not job.get("notes") and bool(notes_str)
        if not job.get("fit"):
            job["fit"] = fit_str
        if not job.get("notes"):
            job["notes"] = notes_str
        stats["enriched"] += 1
        log.debug(
            "[enrich-fit-notes] %s: pre fit=%r notes=%r -> "
            "got fit=%r notes=%r (wrote_fit=%s wrote_notes=%s)",
            company,
            pre_fit,
            pre_notes,
            fit_str,
            notes_str,
            wrote_fit,
            wrote_notes,
        )

    pool = ThreadPoolExecutor(max_workers=pool_size)
    # See companies path for why this is incremental rather than a bulk update.
    futures: dict = {}
    previous_handler = _install_interrupt_notifier(futures, timeout, "jobs")
    for j in targets:
        futures[
            pool.submit(
                _fetch_fit_notes_for_job,
                j,
                role_persona,
                loc_summary,
                hc,
                config,
                timeout,
            )
        ] = j
    # `applied` tracks futures whose result has been written to `jobs`/`stats`,
    # so the interrupt drain doesn't double-count. The companies path gets the
    # same guarantee for free via its `cache` keyed on unique company names.
    applied: set = set()
    try:
        for fut in as_completed(futures):
            job = futures[fut]
            fit_str, notes_str, failed = fut.result()
            _apply(job, fit_str, notes_str, failed)
            applied.add(id(fut))
        pool.shutdown(wait=True)
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        for fut in futures:
            if id(fut) in applied:
                continue
            if fut.done() and not fut.cancelled():
                job = futures[fut]
                fit_str, notes_str, failed = fut.result()
                _apply(job, fit_str, notes_str, failed)
        raise
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    return stats


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
        {"fit": 5, "notes": ""}

    Example prompt response for "Staff SRE at Acme, Remote":
        {"fit": 8, "notes": "AWS/k8s; fully remote; no comp range listed"}

    Returns a stats dict: {"enriched": N, "skipped_budget": N, "skipped_no_desc": 0, "failed": N}.
    """
    from daily_driver.plugins.job_search.scraper.runner import (
        enrich_timeout,
        enrichment_cfg,
        home_city,
        persona,
    )

    stats = {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}
    if (
        ai_provider.resolve_ai_config(config).enrichment.provider == "claude"
        and shutil.which("claude") is None
    ):
        log.warning("[enrich-fit-notes] claude CLI not found on PATH, skipping")
        return stats

    cfg = enrichment_cfg(config)
    if not cfg.enrich_fit or not cfg.enrich_notes:
        log.debug("[enrich-fit-notes] fit or notes disabled via config")
        return stats

    if budget <= 0:
        budget = cfg.max_enrich_fit
    loc_summary = _location_summary(config)
    role_persona = persona(config)
    hc = home_city(config)

    pool_size = _ollama_pool_size(config)
    eligible_count = sum(
        1
        for j in jobs
        if j.get("status") != "skipped" and not (j.get("fit") and j.get("notes"))
    )
    no_desc = sum(
        1
        for j in jobs
        if j.get("status") != "skipped"
        and not (j.get("fit") and j.get("notes"))
        and not (j.get("description_text") or "").strip()
    )
    log.info(
        "[enrich-fit-notes] enriching up to %d jobs (%d eligible%s)...",
        budget,
        eligible_count,
        f", parallel={pool_size}" if pool_size > 1 else "",
    )
    if no_desc:
        log.info(
            "[enrich-fit-notes] %d eligible jobs have no description_text on row "
            "(notes will be left empty by prompt design — only Fit can be filled)",
            no_desc,
        )

    if pool_size > 1 and config is not None:
        stats = _enrich_fit_and_notes_parallel(
            jobs,
            config,
            budget=budget,
            pool_size=pool_size,
            role_persona=role_persona,
            loc_summary=loc_summary,
            hc=hc,
            stats=stats,
            timeout=enrich_timeout(config),
        )
        log.info(
            "[enrich-fit-notes] done: %d enriched, %d failed, %d skipped (budget)",
            stats["enriched"],
            stats["failed"],
            stats["skipped_budget"],
        )
        return stats

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

        fit_str, notes_str, failed = _fetch_fit_notes_for_job(
            job, role_persona, loc_summary, hc, config, enrich_timeout(config)
        )
        calls_made += 1
        company = job.get("company", "unknown")
        pre_fit = job.get("fit", "")
        pre_notes = job.get("notes", "")
        if failed:
            stats["failed"] += 1
            log.debug(
                "[enrich-fit-notes] %s: call failed, no write " "(pre fit=%r notes=%r)",
                company,
                pre_fit,
                pre_notes,
            )
            continue
        wrote_fit = not job.get("fit") and bool(fit_str)
        wrote_notes = not job.get("notes") and bool(notes_str)
        if not job.get("fit"):
            job["fit"] = fit_str
        if not job.get("notes"):
            job["notes"] = notes_str
        stats["enriched"] += 1
        log.debug(
            "[enrich-fit-notes] %s: pre fit=%r notes=%r -> "
            "got fit=%r notes=%r (wrote_fit=%s wrote_notes=%s)",
            company,
            pre_fit,
            pre_notes,
            fit_str,
            notes_str,
            wrote_fit,
            wrote_notes,
        )

    log.info(
        "[enrich-fit-notes] done: %d enriched, %d failed, %d skipped (budget)",
        stats["enriched"],
        stats["failed"],
        stats["skipped_budget"],
    )
    return stats


def enrich_job_details(jobs: list[dict], config: dict) -> None:
    """Fetch each job's detail page and populate comp/posted_date in place.

    Caches by URL within the run so jobs that share a detail URL only generate
    one HTTP request. Skips jobs that already have `comp` set so listing-card
    sources that already provide salary aren't clobbered. Network/parse errors
    are swallowed — missing data is the expected outcome for boards that don't
    expose JSON-LD, not an error worth aborting the run for.
    """
    from daily_driver.plugins.job_search.scraper.runner import enrichment_cfg

    cache: dict[str, dict] = {}
    cfg = enrichment_cfg(config)
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

    session: Session | None = None
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
    """Typed wrapper around ``enrich_company_descriptions``.

    Round-trips through a working-dict list because the legacy body mutates
    in place; returns a fresh list of frozen EnrichedJob instances built via
    ``model_copy(update=...)``.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import (
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
    """Typed wrapper around ``enrich_fit_and_notes``."""
    from daily_driver.plugins.job_search.scraper.csv_io import (
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
    """Typed wrapper around ``enrich_job_details``.

    The legacy ``enrich_job_details`` returns ``None`` and mutates in place;
    this wrapper produces a fresh list with each job's description_text /
    posted_date / comp populated where the detail fetch supplied them.
    """
    import datetime as dt

    from daily_driver.plugins.job_search.scraper.csv_io import (
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
