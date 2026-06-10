"""Claude/LLM enrichers: company descriptions and fit/notes (+criteria fold).

Both enrichers operate on ``EnrichedJob`` directly and fan out through one
pooled implementation: ``ThreadPoolExecutor(max_workers=pool_size)`` where
``pool_size == 1`` runs the work on the main thread (serial). Each replaces
slots in the passed list with new frozen instances and returns that list plus a
stats dict; the slots are replaced in the caller's own list (not a copy) so a
``KeyboardInterrupt`` mid-pass leaves enriched-so-far results in that list for
a caller that persists them — backfill does (its interrupt handler rewrites
jobs.csv); run() does not flush mid-run yet (Wave 3). Out-of-range LLM fit
scores are clamped at the model boundary with a logged warning rather than
silently stored.
"""

from __future__ import annotations

import json
import re
import shutil
import signal
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from daily_driver.plugins.job_search.scraper.models import (
    ENRICH_SKIP_STATUSES,
    EnrichedJob,
    clamp_fit,
)

if TYPE_CHECKING:
    from daily_driver.core.progress import ProgressCallback
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
            "enrichment", prompt, ai=ctx.ai, timeout=timeout
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


def enrich_company_descriptions(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    *,
    budget: int = 0,
    progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, int]]:
    """Populate Product/Purpose and GD Rating using the configured AI provider.

    One provider call per unique company name; results cached within the run.
    Logs a warning if the ``claude`` CLI is the provider and not on PATH.

    Budget limits total provider calls to avoid silent stalls on slow networks —
    each call blocks for up to enrich_timeout seconds (default 30s). Fans out
    through ``ThreadPoolExecutor`` when the provider's ``max_parallel > 1``;
    ``pool_size == 1`` runs serially on the main thread.

    Returns ``(jobs, stats)`` with stats keys: enriched, skipped_cached, failed.
    """
    stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
    # Replace slots in the caller's list (not a copy) so a KeyboardInterrupt
    # mid-pass leaves enriched-so-far results for a caller that persists them
    # (backfill rewrites on interrupt; run() does not flush mid-run yet).
    out = jobs
    if ctx.ai.enrichment.provider == "claude" and shutil.which("claude") is None:
        log.warning("[enrich] claude CLI not found on PATH, skipping product lookup")
        return out, stats

    cfg = ctx.plugin.enrichment
    if budget <= 0:
        budget = cfg.max_enrich_companies
    include_gd = cfg.enrich_gd_rating
    timeout = cfg.enrich_timeout

    unique_companies = {
        job.company.strip()
        for job in out
        if not job.product_filled and job.company.strip()
    }
    pool_size = _enrich_pool_size(ctx)
    log.info(
        "[enrich] enriching up to %d companies (%d unique%s)...",
        budget,
        len(unique_companies),
        f", parallel={pool_size}" if pool_size > 1 else "",
    )

    companies = list(unique_companies)[:budget]
    if len(unique_companies) > budget:
        log.warning(
            "[enrich] budget reached (%d), %d companies unenriched",
            budget,
            len(unique_companies) - budget,
        )

    # company -> (product, gd_rating); only fetched companies appear, so the
    # stitch below leaves un-fetched (budget-dropped) companies untouched.
    cache: dict[str, dict[str, str]] = {}

    def _stitch() -> None:
        for i, job in enumerate(out):
            if job.product_filled:
                stats["skipped_cached"] += 1
                continue
            company = job.company.strip()
            cached = cache.get(company)
            if not cached:
                continue
            updates: dict[str, Any] = {}
            if cached.get("product"):
                updates["product"] = cached["product"]
            if cached.get("gd_rating") and not job.gd_rating:
                updates["gd_rating"] = cached["gd_rating"]
            if not updates:
                continue
            # A validation failure (or any non-interrupt error) applying one
            # result must fail only that job, never abort the whole run — run()
            # has no incremental flush, so an escape would lose every job.
            try:
                out[i] = job.with_updates(**updates)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                log.warning(
                    "%s company=%s: dropping enrichment update (%s)",
                    _enrich_tag("enrich"),
                    company,
                    exc,
                )
                continue
            if "product" in updates:
                stats["enriched"] += 1

    if pool_size > 1:
        pool = ThreadPoolExecutor(max_workers=pool_size)
        # Populate `futures` incrementally so the SIGINT handler always sees a
        # mapping that reflects what's actually been submitted. A bulk replace
        # would leave a window where the handler reads {} and reports "0".
        futures: dict[Any, str] = {}
        previous_handler = _install_interrupt_notifier(futures, timeout, "companies")
        for c in companies:
            futures[pool.submit(_fetch_company_info, c, ctx, include_gd, timeout)] = c
        done_count = 0
        heartbeat = max(1, len(companies) // 5)
        try:
            for fut in as_completed(futures):
                company = futures[fut]
                product, gd_rating, failed = fut.result()
                cache[company] = {"product": product, "gd_rating": gd_rating}
                if failed:
                    stats["failed"] += 1
                if progress is not None:
                    progress(1, company)
                done_count += 1
                if done_count % heartbeat == 0 or done_count == len(companies):
                    log.info(
                        "%s %d/%d companies done (%d failed)",
                        _enrich_tag("enrich"),
                        done_count,
                        len(companies),
                        stats["failed"],
                    )
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
                        if progress is not None:
                            progress(1, company)
            _stitch()
            raise
        finally:
            signal.signal(signal.SIGINT, previous_handler)
        _stitch()
        return out, stats

    for company in companies:
        product, gd_rating, failed = _fetch_company_info(
            company, ctx, include_gd, timeout
        )
        cache[company] = {"product": product, "gd_rating": gd_rating}
        if failed:
            stats["failed"] += 1
        if progress is not None:
            progress(1, company)
    _stitch()
    return out, stats


def _location_summary(ctx: ScrapeContext) -> str:
    from daily_driver.plugins.job_search.scraper.countries import country_names
    from daily_driver.plugins.job_search.scraper.runner import home_city

    loc_cfg = ctx.plugin.locations
    parts = [f"Based in: {home_city(ctx.plugin)}"]
    if loc_cfg is None:
        parts.append("Remote: yes")
        return "; ".join(parts)
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
    job: EnrichedJob,
    role_persona: str,
    loc_summary: str,
    hc: str,
    criteria: Sequence[Criterion] = (),
    context: str = "",
) -> str:
    role = job.role or "unknown"
    company = job.company or "unknown"
    location = job.location or "unknown"
    product = job.product_or_blank
    desc = job.description_text

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


def _clamp_fit(raw: float, company: str, role: str) -> int:
    """Clamp an LLM fit score to the model's 1-10 bound, logging any clamp.

    Out-of-range LLM scores are clamped rather than rejected so a usable signal
    survives a sloppy provider response; the raw value stays visible at -v.
    """
    score = int(raw)
    clamped = clamp_fit(score)
    if clamped != score:
        log.warning(
            "%s company=%s role=%s: fit %d out of range [1,10], clamped to %d",
            _enrich_tag("enrich-fit-notes"),
            company,
            role,
            score,
            clamped,
        )
    return clamped


def _fetch_fit_notes_for_job(
    job: EnrichedJob,
    role_persona: str,
    loc_summary: str,
    hc: str,
    ctx: ScrapeContext,
    timeout: int,
    criteria: Sequence[Criterion] = (),
    context: str = "",
) -> tuple[int | None, str, bool]:
    """Worker: fetch fit/notes for one job. Returns (fit, notes, failed).

    ``fit`` is a validated 1-10 integer (clamped from out-of-range values) or
    ``None`` when the call failed.
    """
    company = job.company or "unknown"
    role = job.role or "unknown"
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
            "enrichment", prompt, ai=ctx.ai, timeout=timeout
        )
    except AITimeoutError as exc:
        log.warning(
            "%s %s: AI provider timed out after %ds",
            _enrich_tag("enrich-fit-notes"),
            company,
            exc.timeout_seconds or timeout,
        )
        return None, "", True
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
        return None, "", True
    except (FileNotFoundError, PermissionError, claude_cli.ClaudeNotFoundError) as exc:
        log.warning("%s %s failed: %s", _enrich_tag("enrich-fit-notes"), company, exc)
        return None, "", True

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
        return None, "", True

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
        return None, "", True

    score = _clamp_fit(fit_val, company, role)
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
    return score, notes_str, False


def _fit_notes_eligible(job: EnrichedJob) -> bool:
    """A job still wants a fit/notes call: active status, missing fit or notes."""
    return job.status.value not in ENRICH_SKIP_STATUSES and not (job.fit and job.notes)


def enrich_fit_and_notes(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    *,
    budget: int = 0,
    progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, int]]:
    """Populate Fit score and Notes for new jobs via one provider call per job.

    One call per job returns strict JSON ``{"fit": <int 1-10>, "notes": "..."}``.
    Budget caps the combined call count (uses ``max_enrich_fit`` as the limit).
    Fit is scored from role/company/location alone, so description is optional;
    when absent, notes is left empty (not confabulated). Fans out through
    ``ThreadPoolExecutor`` when ``max_parallel > 1``; ``pool_size == 1`` runs
    serially on the main thread.

    Returns ``(jobs, stats)`` with stats keys: enriched, skipped_budget,
    skipped_no_desc (always 0; retained for shape compatibility), failed.
    """
    from daily_driver.plugins.job_search.scraper.runner import home_city

    stats = {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}
    # Replace slots in the caller's list (not a copy) so a KeyboardInterrupt
    # mid-pass leaves enriched-so-far results for a caller that persists them
    # (backfill rewrites on interrupt; run() does not flush mid-run yet).
    out = jobs
    if ctx.ai.enrichment.provider == "claude" and shutil.which("claude") is None:
        log.warning("[enrich-fit-notes] claude CLI not found on PATH, skipping")
        return out, stats

    cfg = ctx.plugin.enrichment
    if not cfg.enrich_fit or not cfg.enrich_notes:
        log.debug("[enrich-fit-notes] fit or notes disabled via config")
        return out, stats

    if budget <= 0:
        budget = cfg.max_enrich_fit
    loc_summary = _location_summary(ctx)
    role_persona = ctx.plugin.persona or "SRE/Platform/Infra engineer"
    hc = home_city(ctx.plugin)
    crit_list = list(cfg.criteria)
    context_text = ctx.context_text
    timeout = cfg.enrich_timeout

    pool_size = _enrich_pool_size(ctx)
    # Eligible indices preserve input order so budget truncation matches the
    # former first-N-by-position behavior.
    eligible_idx = [i for i, j in enumerate(out) if _fit_notes_eligible(j)]
    eligible_count = len(eligible_idx)
    if context_text:
        # context.md rides every per-job enrichment prompt; a large file
        # multiplies token cost across the run, so surface the projection.
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
    no_desc = sum(1 for i in eligible_idx if not out[i].description_text.strip())
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

    target_idx = eligible_idx[:budget]
    if eligible_count > budget:
        log.warning(
            "[enrich-fit-notes] budget reached (%d), skipping %d jobs",
            budget,
            eligible_count - budget,
        )
        stats["skipped_budget"] += eligible_count - budget

    def _apply(idx: int, fit: int | None, notes_str: str, failed: bool) -> None:
        job = out[idx]
        company = job.company or "unknown"
        if failed:
            stats["failed"] += 1
            log.debug(
                "[enrich-fit-notes] %s: call failed, no write (pre fit=%r notes=%r)",
                company,
                job.fit,
                job.notes,
            )
            return
        updates: dict[str, Any] = {}
        wrote_fit = not job.fit and fit is not None
        wrote_notes = not job.notes and bool(notes_str)
        if wrote_fit:
            updates["fit"] = fit
        if wrote_notes:
            updates["notes"] = notes_str
        if updates:
            # Validation (or any non-interrupt error) applying one result must
            # fail only this job, never abort the run — run() has no incremental
            # flush, so an escape would lose every scraped+enriched job.
            try:
                out[idx] = job.with_updates(**updates)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                log.warning(
                    "[enrich-fit-notes] %s: dropping enrichment update (%s)",
                    company,
                    exc,
                )
                return
        stats["enriched"] += 1
        log.debug(
            "[enrich-fit-notes] %s: pre fit=%r notes=%r -> got fit=%r notes=%r "
            "(wrote_fit=%s wrote_notes=%s)",
            company,
            job.fit,
            job.notes,
            fit,
            notes_str,
            wrote_fit,
            wrote_notes,
        )

    def _fetch(idx: int) -> tuple[int | None, str, bool]:
        return _fetch_fit_notes_for_job(
            out[idx],
            role_persona,
            loc_summary,
            hc,
            ctx,
            timeout,
            crit_list,
            context_text,
        )

    if pool_size > 1:
        pool = ThreadPoolExecutor(max_workers=pool_size)
        # See enrich_company_descriptions for why this is incremental.
        futures: dict[Any, int] = {}
        previous_handler = _install_interrupt_notifier(futures, timeout, "jobs")
        for idx in target_idx:
            futures[pool.submit(_fetch, idx)] = idx
        # `applied` tracks futures already written so the interrupt drain doesn't
        # double-count.
        applied: set[int] = set()
        done_count = 0
        heartbeat = max(1, len(target_idx) // 5)
        try:
            for fut in as_completed(futures):
                idx = futures[fut]
                fit, notes_str, failed = fut.result()
                _apply(idx, fit, notes_str, failed)
                applied.add(id(fut))
                if progress is not None:
                    progress(1, out[idx].company)
                done_count += 1
                if done_count % heartbeat == 0 or done_count == len(target_idx):
                    log.info(
                        "%s %d/%d jobs done (%d failed)",
                        _enrich_tag("enrich-fit-notes"),
                        done_count,
                        len(target_idx),
                        stats["failed"],
                    )
            pool.shutdown(wait=True)
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            for fut in futures:
                if id(fut) in applied:
                    continue
                if fut.done() and not fut.cancelled():
                    idx = futures[fut]
                    fit, notes_str, failed = fut.result()
                    _apply(idx, fit, notes_str, failed)
                    if progress is not None:
                        progress(1, out[idx].company)
            raise
        finally:
            signal.signal(signal.SIGINT, previous_handler)
        log.info(
            "[enrich-fit-notes] done: %d enriched, %d failed, %d skipped (budget)",
            stats["enriched"],
            stats["failed"],
            stats["skipped_budget"],
        )
        return out, stats

    for idx in target_idx:
        fit, notes_str, failed = _fetch(idx)
        if progress is not None:
            progress(1, out[idx].company)
        _apply(idx, fit, notes_str, failed)

    log.info(
        "[enrich-fit-notes] done: %d enriched, %d failed, %d skipped (budget)",
        stats["enriched"],
        stats["failed"],
        stats["skipped_budget"],
    )
    return out, stats
