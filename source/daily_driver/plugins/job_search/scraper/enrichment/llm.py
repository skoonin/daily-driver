"""Claude/LLM enrichers: company descriptions and fit/notes (+criteria fold)."""

from __future__ import annotations

import json
import re
import shutil
import signal
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from daily_driver.core.logging import get_logger
from daily_driver.integrations import ai_provider, claude_cli
from daily_driver.integrations.ai_provider import AIInvocationError, AITimeoutError
from daily_driver.plugins.job_search.config import Criterion
from daily_driver.plugins.job_search.scraper.enrichment._shared import (
    _enrich_pool_size,
    _enrich_tag,
    _install_interrupt_notifier,
)
from daily_driver.plugins.job_search.scraper.models import ENRICH_SKIP_STATUSES

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)


def _fetch_company_info(
    company: str,
    ctx: ScrapeContext,
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
            "enrichment", prompt, ai=ctx.ai, timeout=timeout, format_json=False
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
    jobs: list[dict[str, Any]],
    ctx: ScrapeContext,
    *,
    budget: int,
    include_gd: bool,
    pool_size: int,
    unique_companies: set[str],
    stats: dict[str, int],
    timeout: int,
) -> dict[str, int]:
    """Parallel path for enrich_company_descriptions (claude or ollama)."""
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
    futures: dict[Any, str] = {}
    previous_handler = _install_interrupt_notifier(futures, timeout, "companies")
    for c in companies:
        futures[pool.submit(_fetch_company_info, c, ctx, include_gd, timeout)] = c
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
    jobs: list[dict[str, Any]], ctx: ScrapeContext, *, budget: int = 0
) -> dict[str, int]:
    """Populate Product/Purpose and GD Rating in-place using the Claude CLI.

    One `claude -p` call per unique company name; results cached within the run.
    Logs a warning if the `claude` CLI is not on PATH.

    Budget limits total claude calls to avoid silent stalls on slow networks —
    each call blocks for up to enrich_timeout seconds (default 30s).

    Returns a stats dict with keys: enriched, skipped_cached, failed.
    """
    stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
    if ctx.ai.enrichment.provider == "claude" and shutil.which("claude") is None:
        log.warning("[enrich] claude CLI not found on PATH, skipping product lookup")
        return stats

    cfg = ctx.plugin.enrichment
    if budget <= 0:
        budget = cfg.max_enrich_companies
    include_gd = cfg.enrich_gd_rating

    unique_companies = {
        job.get("company", "").strip()
        for job in jobs
        if not job.get("product") and job.get("company", "").strip()
    }
    pool_size = _enrich_pool_size(ctx)
    log.info(
        "[enrich] enriching up to %d companies (%d unique%s)...",
        budget,
        len(unique_companies),
        f", parallel={pool_size}" if pool_size > 1 else "",
    )

    if pool_size > 1:
        return _enrich_company_descriptions_parallel(
            jobs,
            ctx,
            budget=budget,
            include_gd=include_gd,
            pool_size=pool_size,
            unique_companies=unique_companies,
            stats=stats,
            timeout=cfg.enrich_timeout,
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
                    company, ctx, include_gd, cfg.enrich_timeout
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


def _location_summary(ctx: ScrapeContext) -> str:
    from daily_driver.plugins.job_search.scraper.runner import home_city
    from daily_driver.plugins.job_search.scraper.sources._http import country_names

    loc_cfg = ctx.plugin.locations
    parts = [f"Based in: {home_city(ctx.plugin)}"]
    if loc_cfg is None:
        parts.append("Remote: yes")
        return "; ".join(parts)
    if loc_cfg.cities:
        parts.append("Preferred cities: " + ", ".join(loc_cfg.cities))
    if loc_cfg.countries:
        # Aliases are stored lowercase for matching; the longest is the full
        # country name (not the "usa"/"uk" abbrev). Title-case it for the prompt.
        names = [
            max(country_names(c), key=len, default=c).title() for c in loc_cfg.countries
        ]
        parts.append("Countries: " + ", ".join(names))
    if loc_cfg.remote:
        parts.append("Remote: yes")
    return "; ".join(parts)


# Criterion values that mean "nothing to surface" — folded results equal to one
# of these (case-insensitive) add no Notes noise, keeping the common case quiet.
_CRITERIA_SKIP_VALUES = {"unknown", "n/a"}

# Warn when an injected context.md exceeds this rough token estimate, since it
# rides every per-job enrichment prompt and multiplies cost across the run.
_CONTEXT_WARN_TOKENS = 1500


def _fold_criteria_values(
    notes: str, criteria: Sequence[Criterion], values: dict[str, Any]
) -> str:
    """Append meaningful criterion values to a job's notes, quiet by default.

    Each configured criterion whose LLM-returned value is meaningful becomes a
    "Label: value" segment joined onto ``notes`` with " | ". Empty / "unknown"
    / "n/a" values (case-insensitive, see ``_CRITERIA_SKIP_VALUES``) and
    non-string values are skipped so the common case adds nothing.
    """
    for c in criteria:
        value = values.get(c.label)
        if not isinstance(value, str):
            continue
        # Collapse any embedded newlines/runs of whitespace the LLM may emit so
        # the folded segment stays single-line for Notes/CSV.
        cleaned = " ".join(value.split())
        if not cleaned or cleaned.lower() in _CRITERIA_SKIP_VALUES:
            continue
        notes += f" | {c.label}: {cleaned}" if notes else f"{c.label}: {cleaned}"
    return notes


def _build_fit_notes_prompt(
    job: dict[str, Any],
    role_persona: str,
    loc_summary: str,
    hc: str,
    criteria: Sequence[Criterion] = (),
    context: str = "",
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
        "You are evaluating how well a specific candidate fits a job. Be honest "
        "and calibrated -- most jobs are average fits. Reserve 8-10 for genuinely "
        "strong matches, 1-3 for clear mismatches.\n"
        f"The candidate is targeting: {role_persona}, based in {hc}.\n"
        f"Location preferences: {loc_summary}\n"
    )
    # The candidate's full context.md is injected when the workspace has one;
    # without it, fit falls back to role/company/location signal alone.
    if context:
        prompt += (
            "\nCANDIDATE CONTEXT -- their full background, experience, and "
            "preferences. Treat as authoritative; the candidate may deliberately "
            "repeat points they consider especially important:\n"
            f"{context}\n"
        )
    prompt += f"\nJob: {role} at {company}, {location}\n"
    if product:
        prompt += f"Company: {product}\n"

    # The JSON shape gains a "criteria" object only when criteria are configured.
    shape = '{"fit": <integer 1-10>, "notes": "<one line, max 20 words>"'
    shape += (
        ', "criteria": {<one entry per criterion below, keyed by its label>}}'
        if criteria
        else "}"
    )

    if context:
        fit_instr = (
            "fit: score 1-10, weighting (1) experience match -- how well the "
            "candidate's background, the systems they've built, and their skills "
            "match the role's real requirements (weigh this most; reward direct "
            "overlap, penalize roles centered on their stated gap areas or far "
            "from their level and track); (2) location fit vs. the preferences "
            "above; (3) seniority and track match. If the description is too thin "
            "to judge experience match, return 5.\n"
        )
    else:
        fit_instr = (
            "fit: rate 1-10 how well this role fits the candidate based on "
            "role/company/location. If you truly lack enough information to "
            "assess, return 5.\n"
        )

    prompt += (
        f"{desc_section}\n\n"
        "Reply with ONLY valid JSON on a single line, exactly this shape:\n"
        f"{shape}\n\n"
        f"{fit_instr}"
        "notes: one line justifying the score -- name the decisive match or "
        "mismatch and the location fit. Do not list the tech stack. If the "
        "description is absent, return notes as an empty string. Do not guess or "
        "hallucinate notes when you have no description.\n"
    )

    if criteria:
        criteria_lines = "\n".join(f"- {c.label}: {c.assess}" for c in criteria)
        prompt += (
            "Below are EXTRA criteria the candidate has asked you to assess for "
            "each job, on top of fit and notes. Evaluate each strictly against the "
            "job description and return a short value (a few words) in the "
            '"criteria" object, keyed by its label. Answer "unknown" when the '
            "description is silent or absent -- do not guess. The criteria:\n"
            f"{criteria_lines}\n"
        )

    prompt += (
        "Do not include any preamble, explanation, or markdown -- only the JSON object."
    )
    return prompt


def _fetch_fit_notes_for_job(
    job: dict[str, Any],
    role_persona: str,
    loc_summary: str,
    hc: str,
    ctx: ScrapeContext,
    timeout: int,
    criteria: Sequence[Criterion] = (),
    context: str = "",
) -> tuple[str, str, bool]:
    """Worker: fetch fit/notes for one job. Returns (fit_str, notes_str, failed)."""
    company = job.get("company", "unknown")
    role = job.get("role", "unknown")
    prompt = _build_fit_notes_prompt(
        job, role_persona, loc_summary, hc, criteria, context
    )
    log.debug(
        "%s company=%s role=%s prompt=%r",
        _enrich_tag("enrich-fit-notes"),
        company,
        role,
        prompt,
    )

    try:
        stdout = ai_provider.invoke_for(
            "enrichment", prompt, ai=ctx.ai, timeout=timeout, format_json=True
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
    if criteria:
        criteria_obj = parsed.get("criteria")
        if isinstance(criteria_obj, dict):
            notes_str = _fold_criteria_values(notes_str, criteria, criteria_obj)
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
    jobs: list[dict[str, Any]],
    ctx: ScrapeContext,
    *,
    budget: int,
    pool_size: int,
    role_persona: str,
    loc_summary: str,
    hc: str,
    stats: dict[str, int],
    timeout: int,
    criteria: Sequence[Criterion] = (),
    context: str = "",
) -> dict[str, int]:
    """Parallel path for enrich_fit_and_notes (claude or ollama)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    eligible = [
        j
        for j in jobs
        if j.get("status") not in ENRICH_SKIP_STATUSES
        and not (j.get("fit") and j.get("notes"))
    ]
    targets = eligible[:budget]
    if len(eligible) > budget:
        log.warning(
            "[enrich-fit-notes] budget reached (%d), skipping %d jobs",
            budget,
            len(eligible) - budget,
        )
        stats["skipped_budget"] += len(eligible) - budget

    def _apply(job: dict[str, Any], fit_str: str, notes_str: str, failed: bool) -> None:
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
    futures: dict[Any, dict[str, Any]] = {}
    previous_handler = _install_interrupt_notifier(futures, timeout, "jobs")
    for j in targets:
        futures[
            pool.submit(
                _fetch_fit_notes_for_job,
                j,
                role_persona,
                loc_summary,
                hc,
                ctx,
                timeout,
                criteria,
                context,
            )
        ] = j
    # `applied` tracks futures whose result has been written to `jobs`/`stats`,
    # so the interrupt drain doesn't double-count. The companies path gets the
    # same guarantee for free via its `cache` keyed on unique company names.
    applied: set[int] = set()
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


def enrich_fit_and_notes(
    jobs: list[dict[str, Any]], ctx: ScrapeContext, *, budget: int = 0
) -> dict[str, int]:
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
    from daily_driver.plugins.job_search.scraper.runner import home_city

    stats = {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}
    if ctx.ai.enrichment.provider == "claude" and shutil.which("claude") is None:
        log.warning("[enrich-fit-notes] claude CLI not found on PATH, skipping")
        return stats

    cfg = ctx.plugin.enrichment
    if not cfg.enrich_fit or not cfg.enrich_notes:
        log.debug("[enrich-fit-notes] fit or notes disabled via config")
        return stats

    if budget <= 0:
        budget = cfg.max_enrich_fit
    loc_summary = _location_summary(ctx)
    role_persona = ctx.plugin.persona or "SRE/Platform/Infra engineer"
    hc = home_city(ctx.plugin)
    crit_list = list(cfg.criteria)
    context_text = ctx.context_text

    pool_size = _enrich_pool_size(ctx)
    eligible_count = sum(
        1
        for j in jobs
        if j.get("status") not in ENRICH_SKIP_STATUSES
        and not (j.get("fit") and j.get("notes"))
    )
    if context_text:
        # context.md rides every per-job enrichment prompt; a large file
        # multiplies token cost across the whole run, so surface the projection.
        ctx_tokens = len(context_text) // 4  # ~4 chars/token heuristic
        if ctx_tokens >= _CONTEXT_WARN_TOKENS:
            jobs_to_enrich = min(budget, eligible_count)
            log.warning(
                "[enrich-fit-notes] context.md is ~%d tokens, injected into ~%d "
                "enrichment calls this run (~%d input tokens); trim it if that's "
                "more than you want to spend",
                ctx_tokens,
                jobs_to_enrich,
                ctx_tokens * jobs_to_enrich,
            )
    no_desc = sum(
        1
        for j in jobs
        if j.get("status") not in ENRICH_SKIP_STATUSES
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

    if pool_size > 1:
        stats = _enrich_fit_and_notes_parallel(
            jobs,
            ctx,
            budget=budget,
            pool_size=pool_size,
            role_persona=role_persona,
            loc_summary=loc_summary,
            hc=hc,
            stats=stats,
            timeout=cfg.enrich_timeout,
            criteria=crit_list,
            context=context_text,
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
        if job.get("status") in ENRICH_SKIP_STATUSES:
            continue
        if job.get("fit") and job.get("notes"):
            continue
        if calls_made >= budget:
            remaining = sum(
                1
                for j in jobs[idx:]
                if j.get("status") not in ENRICH_SKIP_STATUSES
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
            job,
            role_persona,
            loc_summary,
            hc,
            ctx,
            cfg.enrich_timeout,
            crit_list,
            context_text,
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
