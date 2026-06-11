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
import math
import re
import shutil
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from daily_driver.core.logging import get_logger
from daily_driver.integrations import ai_provider, claude_cli
from daily_driver.integrations.ai_provider import AIInvocationError, AITimeoutError
from daily_driver.plugins.job_search.config import Criterion
from daily_driver.plugins.job_search.scraper.enrichment._shared import (
    _enrich_pool_size,
    _enrich_tag,
    _install_interrupt_notifier,
    _restore_interrupt_handler,
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

# Ollama serializes calls beyond OLLAMA_NUM_PARALLEL, so queued requests still
# count against the per-call timeout — a wide fan-out can time out purely from
# queueing. The remedy is server-side, so the full hint is logged ONCE per run
# (not per timed-out call, which would flood the log). Guarded by a lock since
# enrichment fans out across a thread pool.
_OLLAMA_HINT_LOCK = threading.Lock()
_ollama_hint_logged = False

_OLLAMA_QUEUE_HINT = (
    "queued requests count against the timeout; ensure OLLAMA_NUM_PARALLEL >= "
    "max_parallel on the server or set ai.ollama.max_parallel: 1"
)


def _reset_ollama_hint() -> None:
    """Re-arm the once-per-run ollama queue hint at the start of an enrich run."""
    global _ollama_hint_logged
    with _OLLAMA_HINT_LOCK:
        _ollama_hint_logged = False


def _log_provider_timeout(tag: str, label: str, provider: str, seconds: int) -> None:
    """Log a provider-named timeout; for ollama, append the queue hint once."""
    global _ollama_hint_logged
    log.warning("%s %s: %s timed out after %ds", tag, label, provider, seconds)
    if provider != "ollama":
        return
    with _OLLAMA_HINT_LOCK:
        if _ollama_hint_logged:
            return
        _ollama_hint_logged = True
    log.warning("%s %s", tag, _OLLAMA_QUEUE_HINT)


def _fetch_company_info(
    company: str,
    ctx: ScrapeContext,
    include_product: bool,
    include_gd: bool,
    timeout: int,
) -> tuple[str, str, bool]:
    """Fetch product/GD-rating for one company. Returns (product, gd_rating, failed).

    Worker function: catches all expected exceptions and returns failed=True
    instead of propagating, so it is safe to call from a thread pool. The prompt
    requests only the enabled field(s); a disabled field is never asked for or
    written.
    """
    if include_product and include_gd:
        prompt = (
            f"Answer in exactly 2 lines, no preamble:\n"
            f"Line 1: What does {company} build or do? (max 12 words)\n"
            f"Line 2: Glassdoor rating (e.g. 4.1), or 'unknown' if unsure."
        )
    elif include_product:
        prompt = (
            f"In one sentence (max 12 words), what does {company} build or do? "
            "Answer only, no preamble."
        )
    else:
        prompt = (
            f"What is {company}'s Glassdoor rating (e.g. 4.1)? "
            "Answer with the number only, or 'unknown' if unsure. No preamble."
        )
    enrichment_cfg = ctx.plugin.enrichment
    try:
        stdout = ai_provider.invoke_for(
            prompt,
            provider=enrichment_cfg.provider,
            model=enrichment_cfg.model,
            ai=ctx.ai,
            timeout=timeout,
        )
    except AITimeoutError as exc:
        _log_provider_timeout(
            _enrich_tag("enrich"),
            company,
            exc.provider,
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
    product = lines[0] if (include_product and lines) else ""
    gd_rating = ""
    if include_gd:
        # Both fields: rating is line 2. GD-only: rating is line 1 (no product
        # line was requested).
        gd_line = (
            lines[1]
            if (include_product and len(lines) >= 2)
            else (lines[0] if lines else "")
        )
        gd_rating = _parse_gd_rating(gd_line, company)

    # In GD-only mode the rating is the call's sole product; an unparseable
    # answer means the call yielded nothing useful, so count it as a failure
    # rather than caching an empty rating silently. (With product enabled the
    # product line still carries value, so it is not a failure.)
    failed = include_gd and not include_product and not gd_rating
    return product, gd_rating, failed


def _parse_gd_rating(line: str, company: str) -> str:
    """Extract a Glassdoor rating from one response line ('' when none found).

    Logs a miss (info) when a non-empty, non-"unknown" line yields no rating via
    either the float parse or the regex fallback — that's a silent gap otherwise.
    """
    raw = line.strip().lower()
    if not raw:
        return ""
    if raw == "unknown":
        return "unknown"
    try:
        return str(round(float(raw), 1))
    except ValueError:
        m = re.search(r"([0-5]\.\d)", line.strip())
        if m:
            log.info(
                "%s GD rating fallback regex extracted %s for %s",
                _enrich_tag("enrich"),
                m.group(1),
                company,
            )
            return m.group(1)
    log.info(
        "%s GD rating not parseable for %s from line %r",
        _enrich_tag("enrich"),
        company,
        line.strip()[:120],
    )
    return ""


@dataclass
class _CompanyPlan:
    """Resolved work for the company-description enricher.

    Bundles the mutable state (``out`` slot list, ``stats``, per-company
    ``cache``) and the per-company fetch/consume/stitch closures so the same
    plan can be driven by the standalone pool, the serial path, or the shared
    coordinator that overlaps this enricher with fit/notes (F1). All state
    mutation happens on whichever single thread drives the consume/stitch
    closures.
    """

    out: list[EnrichedJob]
    stats: dict[str, int]
    companies: list[str]
    cache: dict[str, dict[str, str]]
    timeout: int
    fetch: Callable[[str], tuple[str, str, bool]]
    consume: Callable[[Future[Any], str], None]
    stitch: Callable[[], None]


def _build_company_plan(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    budget: int,
    progress: ProgressCallback | None,
) -> _CompanyPlan | None:
    """Resolve the company enricher's work, or ``None`` to skip the pass.

    Returns ``None`` when both `enrich_product` and `enrich_gd_rating` are
    disabled (nothing to fetch), or (and logs) when the claude provider is
    configured but its CLI is not on PATH — the caller then returns the
    untouched jobs.
    """
    stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
    # Replace slots in the caller's list (not a copy) so a KeyboardInterrupt
    # mid-pass leaves enriched-so-far results for a caller that persists them
    # (backfill rewrites on interrupt; run() does not flush mid-run yet).
    out = jobs
    cfg = ctx.plugin.enrichment
    include_product = cfg.enrich_product
    include_gd = cfg.enrich_gd_rating
    if not include_product and not include_gd:
        log.debug("[enrich] product and gd_rating both disabled via config")
        return None
    if cfg.provider == "claude" and shutil.which("claude") is None:
        log.warning("[enrich] claude CLI not found on PATH, skipping product lookup")
        return None

    if budget <= 0:
        budget = cfg.max_enrich_companies
    timeout = cfg.enrich_timeout

    # A company needs a call when an enabled field is still missing on its row.
    unique_companies = {
        job.company.strip()
        for job in out
        if job.company.strip()
        and (
            (include_product and not job.product_filled)
            or (include_gd and not job.gd_rating)
        )
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
    applied: set[int] = set()
    done_count = [0]
    heartbeat = max(1, len(companies) // 5)

    def _fetch(company: str) -> tuple[str, str, bool]:
        return _fetch_company_info(company, ctx, include_product, include_gd, timeout)

    def _consume(fut: Future[Any], company: str) -> None:
        # `applied` makes re-consume (interrupt drain after the main loop already
        # consumed a future) idempotent: no double cache-write or done_count bump.
        if id(fut) in applied:
            return
        applied.add(id(fut))
        product, gd_rating, failed = fut.result()
        if company not in cache:
            cache[company] = {"product": product, "gd_rating": gd_rating}
            if failed:
                stats["failed"] += 1
            if progress is not None:
                progress(1, company)
        done_count[0] += 1
        if done_count[0] % heartbeat == 0 or done_count[0] == len(companies):
            log.info(
                "%s %d/%d companies done (%d failed)",
                _enrich_tag("enrich"),
                done_count[0],
                len(companies),
                stats["failed"],
            )

    def _stitch() -> None:
        for i, job in enumerate(out):
            # The cached-skip shortcut applies only when product enrichment is
            # on (product_filled means no further product work). With product
            # disabled, a product-filled job may still need its gd_rating.
            if include_product and job.product_filled:
                stats["skipped_cached"] += 1
                continue
            company = job.company.strip()
            cached = cache.get(company)
            if not cached:
                continue
            updates: dict[str, Any] = {}
            if include_product and cached.get("product"):
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
            # Count any written field: a GD-only run writes only gd_rating, so
            # gating on "product" alone would report "0 enriched" for a working
            # run.
            stats["enriched"] += 1

    return _CompanyPlan(
        out=out,
        stats=stats,
        companies=companies,
        cache=cache,
        timeout=timeout,
        fetch=_fetch,
        consume=_consume,
        stitch=_stitch,
    )


def enrich_company_descriptions(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    *,
    budget: int = 0,
    progress: ProgressCallback | None = None,
    _reset_hint: bool = True,
) -> tuple[list[EnrichedJob], dict[str, int]]:
    """Populate Product/Purpose and GD Rating using the configured AI provider.

    One provider call per unique company name; results cached within the run.
    Logs a warning if the ``claude`` CLI is the provider and not on PATH.

    Budget limits total provider calls to avoid silent stalls on slow networks —
    each call blocks for up to enrich_timeout seconds (default 30s). Fans out
    through ``ThreadPoolExecutor`` when the provider's ``max_parallel > 1``;
    ``pool_size == 1`` runs serially on the main thread.

    ``_reset_hint`` re-arms the once-per-run ollama queue hint; the concurrent
    coordinator already arms it once for both phases and passes False so a serial
    run can't emit the hint twice.

    Returns ``(jobs, stats)`` with stats keys: enriched, skipped_cached, failed.
    """
    if _reset_hint:
        _reset_ollama_hint()
    plan = _build_company_plan(jobs, ctx, budget, progress)
    if plan is None:
        return jobs, {"enriched": 0, "skipped_cached": 0, "failed": 0}

    pool_size = _enrich_pool_size(ctx)
    if pool_size > 1:
        # `with` guarantees the pool is joined even on the exception path so a
        # crash can't hang at atexit; the KI branch still cancels pending work
        # before the with-exit (a harmless second shutdown).
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            # Populate `futures` incrementally so the SIGINT handler always sees a
            # mapping that reflects what's been submitted. A bulk replace would
            # leave a window where the handler reads {} and reports "0".
            futures: dict[Any, str] = {}
            previous_handler = _install_interrupt_notifier(
                futures, plan.timeout, "companies"
            )
            for c in plan.companies:
                futures[pool.submit(plan.fetch, c)] = c
            try:
                for fut in as_completed(futures):
                    _guarded_consume(
                        plan.consume, fut, futures[fut], "company", plan.stats
                    )
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                for fut in futures:
                    if fut.done() and not fut.cancelled():
                        # Drain must never replace the KeyboardInterrupt with a
                        # result-application error: swallow + continue so KI
                        # always re-raises.
                        _guarded_consume(
                            plan.consume, fut, futures[fut], "company", plan.stats
                        )
                plan.stitch()
                raise
            finally:
                _restore_interrupt_handler(previous_handler)
        plan.stitch()
        return plan.out, plan.stats

    # Keep every settled Future referenced for the loop's lifetime: consume()
    # de-dupes on id(fut), and a GC'd-then-recycled id would make a later result
    # look already-consumed and silently drop it.
    settled: list[Future[Any]] = []
    for company in plan.companies:
        settled_fut: Future[Any] = Future()
        settled_fut.set_result(plan.fetch(company))
        settled.append(settled_fut)
        plan.consume(settled_fut, company)
    plan.stitch()
    return plan.out, plan.stats


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
    include_remote: bool = False,
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

    # The JSON shape gains a "remote" field (when enabled) and a "criteria"
    # object (when criteria are configured), each only when asked for.
    shape = '{"fit": <integer 1-10>, "notes": "<one line, max 20 words>"'
    if include_remote:
        shape += ', "remote": "remote" | "hybrid" | "onsite"'
    if criteria:
        shape += ', "criteria": {<one entry per criterion below, keyed by its label>}'
    shape += "}"

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

    if include_remote:
        prompt += (
            'remote: one of "remote" (fully remote), "hybrid" (some office days), '
            'or "onsite" (in-office), judged from the description. Omit the field '
            "if the description is too thin to tell.\n"
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


def _guarded_consume(
    consume: Callable[[Future[Any], Any], None],
    fut: Future[Any],
    key: Any,
    kind: str,
    stats: dict[str, int],
) -> None:
    """Run a plan's ``consume`` for one future, isolating non-interrupt errors.

    A worker exception surfaces at ``fut.result()`` and any error applying the
    result (e.g. a malformed value reaching ``int()``) would otherwise escape the
    ``as_completed`` loop, skip the company stitch, and crash run() with the whole
    scraped batch lost. Catching here turns it into one counted failure so the
    rest of the batch still enriches. ``KeyboardInterrupt`` always propagates.
    """
    try:
        consume(fut, key)
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        stats["failed"] += 1
        log.warning(
            "%s %s=%r: dropping enrichment result (%s)",
            _enrich_tag("enrich"),
            kind,
            key,
            exc,
        )


def _clamp_fit(raw: float, company: str, role: str) -> int:
    """Clamp an LLM fit score to the model's 1-10 bound, logging any clamp.

    Out-of-range LLM scores are clamped rather than rejected so a usable signal
    survives a sloppy provider response; the raw value stays visible at -v.

    Raises ValueError on a non-finite ``raw`` (NaN/Infinity): ``int()`` of those
    raises anyway, so reject explicitly. Callers gate on ``math.isfinite`` before
    this, so a raise here is the defense-in-depth backstop, not the normal path.
    """
    if not math.isfinite(raw):
        raise ValueError(f"non-finite fit score: {raw!r}")
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


# Canonical remote values the LLM tier may write; any other answer (or blank)
# leaves the existing cell untouched (tolerant passthrough preserves hand-entry).
_REMOTE_VALUES: frozenset[str] = frozenset({"remote", "hybrid", "onsite"})


def _parse_remote(value: object, *, company: str = "", role: str = "") -> str:
    """Coerce a parsed JSON ``remote`` value to a canonical value, or "" if none.

    Tolerant: a non-string, blank, or unrecognized answer yields "" so the
    caller leaves any existing value (hand-entered or heuristic) in place. A
    non-empty string that fails the enum is logged at debug with company/role and
    the raw value, so a model that never emits canonical values is greppable
    (a blank answer is the normal "not judged" path and is not logged).
    """
    if not isinstance(value, str):
        return ""
    v = value.strip().lower()
    if v in _REMOTE_VALUES:
        return v
    if v:
        log.debug(
            "%s company=%s role=%s: non-canonical remote value %r ignored",
            _enrich_tag("enrich-fit-notes"),
            company or "unknown",
            role or "unknown",
            value,
        )
    return ""


def _fetch_fit_notes_for_job(
    job: EnrichedJob,
    role_persona: str,
    loc_summary: str,
    hc: str,
    ctx: ScrapeContext,
    timeout: int,
    criteria: Sequence[Criterion] = (),
    context: str = "",
    include_remote: bool = False,
) -> tuple[int | None, str, str, bool]:
    """Worker: fetch fit/notes (and remote) for one job.

    Returns ``(fit, notes, remote, failed)``. ``fit`` is a validated 1-10 integer
    (clamped from out-of-range values) or ``None`` when the call failed; ``remote``
    is a canonical value ("remote"/"hybrid"/"onsite") or "" when not judged.
    """
    company = job.company or "unknown"
    role = job.role or "unknown"
    prompt = _build_fit_notes_prompt(
        job, role_persona, loc_summary, hc, criteria, context, include_remote
    )
    log.debug(
        "%s company=%s role=%s prompt=%r",
        _enrich_tag("enrich-fit-notes"),
        company,
        role,
        prompt,
    )

    enrichment_cfg = ctx.plugin.enrichment
    try:
        stdout = ai_provider.invoke_for(
            prompt,
            provider=enrichment_cfg.provider,
            model=enrichment_cfg.model,
            ai=ctx.ai,
            timeout=timeout,
        )
    except AITimeoutError as exc:
        _log_provider_timeout(
            _enrich_tag("enrich-fit-notes"),
            company,
            exc.provider,
            exc.timeout_seconds or timeout,
        )
        return None, "", "", True
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
        return None, "", "", True
    except (FileNotFoundError, PermissionError, claude_cli.ClaudeNotFoundError) as exc:
        log.warning("%s %s failed: %s", _enrich_tag("enrich-fit-notes"), company, exc)
        return None, "", "", True

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
        return None, "", "", True

    fit_val = parsed.get("fit")
    notes_val = parsed.get("notes", "")
    # bool is an int subclass; treat True/False as missing. NaN/Infinity slip
    # through json.loads and the isinstance check but explode in int() — reject
    # them here as a counted failure so one bad response never crashes the batch.
    if (
        not isinstance(fit_val, (int, float))
        or isinstance(fit_val, bool)
        or not math.isfinite(fit_val)
    ):
        log.warning(
            "%s company=%s role=%s: JSON missing valid fit field: %r",
            _enrich_tag("enrich-fit-notes"),
            company,
            role,
            raw[:200],
        )
        return None, "", "", True

    score = _clamp_fit(fit_val, company, role)
    notes_str = notes_val if isinstance(notes_val, str) else ""
    remote = (
        _parse_remote(parsed.get("remote"), company=company, role=role)
        if include_remote
        else ""
    )
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
    return score, notes_str, remote, False


def _fit_notes_eligible(job: EnrichedJob) -> bool:
    """A job still wants a fit/notes call: active status, missing fit or notes."""
    return job.status.value not in ENRICH_SKIP_STATUSES and not (job.fit and job.notes)


@dataclass
class _FitPlan:
    """Resolved work for the fit/notes enricher (mirrors :class:`_CompanyPlan`).

    ``target_idx`` are the eligible job slots to enrich; ``fetch`` runs one
    provider call for a slot and ``consume`` applies its result. All state
    mutation happens on whichever single thread drives ``consume``.
    """

    out: list[EnrichedJob]
    stats: dict[str, int]
    target_idx: list[int]
    timeout: int
    fetch: Callable[[int], tuple[int | None, str, str, bool]]
    consume: Callable[[Future[Any], int], None]


def _build_fit_plan(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    budget: int,
    progress: ProgressCallback | None,
) -> _FitPlan | None:
    """Resolve the fit/notes enricher's work, or ``None`` to skip the pass.

    Returns ``None`` when the claude CLI is missing or fit/notes are disabled in
    config — the caller then returns the untouched jobs with empty stats.
    """
    from daily_driver.plugins.job_search.scraper.runner import home_city

    stats = {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}
    # Replace slots in the caller's list (not a copy) so a KeyboardInterrupt
    # mid-pass leaves enriched-so-far results for a caller that persists them
    # (backfill rewrites on interrupt; run() does not flush mid-run yet).
    out = jobs
    cfg = ctx.plugin.enrichment
    if cfg.provider == "claude" and shutil.which("claude") is None:
        log.warning("[enrich-fit-notes] claude CLI not found on PATH, skipping")
        return None

    if not cfg.enrich_fit or not cfg.enrich_notes:
        log.debug("[enrich-fit-notes] fit or notes disabled via config")
        return None

    if budget <= 0:
        budget = cfg.max_enrich_fit
    loc_summary = _location_summary(ctx)
    role_persona = ctx.plugin.persona or "SRE/Platform/Infra engineer"
    hc = home_city(ctx.plugin)
    crit_list = list(cfg.criteria)
    context_text = ctx.context_text
    timeout = cfg.enrich_timeout
    include_remote = cfg.enrich_is_remote

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

    applied: set[int] = set()
    done_count = [0]
    heartbeat = max(1, len(target_idx) // 5)

    def _apply(
        idx: int, fit: int | None, notes_str: str, remote: str, failed: bool
    ) -> None:
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
        # Remote: the LLM fills a blank cell and may refine the heuristic's coarse
        # "remote" to a definite value; a hand-entered value (anything else) wins
        # and a blank/unknown LLM answer never clobbers an existing value.
        wrote_remote = bool(remote) and job.remote in ("", "remote")
        if wrote_fit:
            updates["fit"] = fit
        if wrote_notes:
            updates["notes"] = notes_str
        if wrote_remote:
            updates["remote"] = remote
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

    def _fetch(idx: int) -> tuple[int | None, str, str, bool]:
        return _fetch_fit_notes_for_job(
            out[idx],
            role_persona,
            loc_summary,
            hc,
            ctx,
            timeout,
            crit_list,
            context_text,
            include_remote,
        )

    def _consume(fut: Future[Any], idx: int) -> None:
        # `applied` guards against the interrupt drain re-applying a slot the
        # main consume loop already wrote.
        if id(fut) in applied:
            return
        fit, notes_str, remote, failed = fut.result()
        _apply(idx, fit, notes_str, remote, failed)
        applied.add(id(fut))
        if progress is not None:
            progress(1, out[idx].company)
        done_count[0] += 1
        if done_count[0] % heartbeat == 0 or done_count[0] == len(target_idx):
            log.info(
                "%s %d/%d jobs done (%d failed)",
                _enrich_tag("enrich-fit-notes"),
                done_count[0],
                len(target_idx),
                stats["failed"],
            )

    return _FitPlan(
        out=out,
        stats=stats,
        target_idx=target_idx,
        timeout=timeout,
        fetch=_fetch,
        consume=_consume,
    )


def enrich_fit_and_notes(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    *,
    budget: int = 0,
    progress: ProgressCallback | None = None,
    _reset_hint: bool = True,
) -> tuple[list[EnrichedJob], dict[str, int]]:
    """Populate Fit score and Notes for new jobs via one provider call per job.

    One call per job returns strict JSON ``{"fit": <int 1-10>, "notes": "..."}``.
    Budget caps the combined call count (uses ``max_enrich_fit`` as the limit).
    Fit is scored from role/company/location alone, so description is optional;
    when absent, notes is left empty (not confabulated). Fans out through
    ``ThreadPoolExecutor`` when ``max_parallel > 1``; ``pool_size == 1`` runs
    serially on the main thread.

    ``_reset_hint`` re-arms the once-per-run ollama queue hint; the concurrent
    coordinator passes False (it arms once for both phases) so a serial run
    can't emit the hint twice.

    Returns ``(jobs, stats)`` with stats keys: enriched, skipped_budget,
    skipped_no_desc (always 0; retained for shape compatibility), failed.
    """
    if _reset_hint:
        _reset_ollama_hint()
    plan = _build_fit_plan(jobs, ctx, budget, progress)
    if plan is None:
        return jobs, {
            "enriched": 0,
            "skipped_budget": 0,
            "skipped_no_desc": 0,
            "failed": 0,
        }

    pool_size = _enrich_pool_size(ctx)
    if pool_size > 1:
        # `with` joins the pool on every exit path (see company enricher).
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            # See enrich_company_descriptions for why this is incremental.
            futures: dict[Any, int] = {}
            previous_handler = _install_interrupt_notifier(
                futures, plan.timeout, "jobs"
            )
            for idx in plan.target_idx:
                futures[pool.submit(plan.fetch, idx)] = idx
            try:
                for fut in as_completed(futures):
                    _guarded_consume(plan.consume, fut, futures[fut], "job", plan.stats)
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                for fut in futures:
                    if fut.done() and not fut.cancelled():
                        # Swallow drain errors so KI always re-raises.
                        _guarded_consume(
                            plan.consume, fut, futures[fut], "job", plan.stats
                        )
                raise
            finally:
                _restore_interrupt_handler(previous_handler)
        log.info(
            "[enrich-fit-notes] done: %d enriched, %d failed, %d skipped (budget)",
            plan.stats["enriched"],
            plan.stats["failed"],
            plan.stats["skipped_budget"],
        )
        return plan.out, plan.stats

    # See enrich_company_descriptions: hold settled Futures so recycled ids can't
    # make consume() drop a later result.
    settled: list[Future[Any]] = []
    for idx in plan.target_idx:
        settled_fut: Future[Any] = Future()
        settled_fut.set_result(plan.fetch(idx))
        settled.append(settled_fut)
        plan.consume(settled_fut, idx)

    log.info(
        "[enrich-fit-notes] done: %d enriched, %d failed, %d skipped (budget)",
        plan.stats["enriched"],
        plan.stats["failed"],
        plan.stats["skipped_budget"],
    )
    return plan.out, plan.stats


def _empty_company_stats() -> dict[str, int]:
    return {"enriched": 0, "skipped_cached": 0, "failed": 0}


def _empty_fit_stats() -> dict[str, int]:
    return {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0}


def enrich_product_and_fit_concurrently(
    jobs: list[EnrichedJob],
    ctx: ScrapeContext,
    *,
    product_budget: int = 0,
    fit_budget: int = 0,
    product_progress: ProgressCallback | None = None,
    fit_progress: ProgressCallback | None = None,
) -> tuple[list[EnrichedJob], dict[str, int], dict[str, int]]:
    """Run the product and fit/notes enrichers overlapped under one shared cap.

    Both enrichers fan their provider calls out through ONE shared executor
    (``max_workers == _enrich_pool_size``), so total concurrent claude/ollama
    subprocesses never exceed the provider's ``max_parallel`` — they share that
    budget rather than each claiming a full pool. The per-enricher budgets
    (``max_enrich_companies`` / ``max_enrich_fit``) stay independent.

    Fit's prompt reads each job's product, which the company enricher only
    stitches in after the merged loop completes; with overlap, fit therefore
    sees a pre-stitch blank product (best-effort context, tolerated by the
    prompt builder). One SIGINT notifier covers the whole overlapped section;
    on interrupt both enrichers drain their finished futures before re-raising.

    All result-application runs on this single coordinator thread, so the two
    per-enricher stats dicts and the shared ``jobs`` slot list are mutated
    without cross-thread races. Returns ``(jobs, product_stats, fit_stats)``.

    Falls back to running the two enrichers serially when the provider is
    serial (``pool_size == 1``) — there is no concurrency to overlap.
    """
    _reset_ollama_hint()
    pool_size = _enrich_pool_size(ctx)
    if pool_size <= 1:
        # Serial provider: nothing to overlap. Run sequentially so behavior
        # (and the per-phase progress bars) matches the non-overlapped path.
        # _reset_hint=False: the coordinator already armed the once-per-run
        # ollama queue hint above, so the sub-enrichers must not re-arm it (a
        # re-arm between phases could emit the hint twice in one serial run).
        out, product_stats = enrich_company_descriptions(
            jobs,
            ctx,
            budget=product_budget,
            progress=product_progress,
            _reset_hint=False,
        )
        out, fit_stats = enrich_fit_and_notes(
            out, ctx, budget=fit_budget, progress=fit_progress, _reset_hint=False
        )
        return out, product_stats, fit_stats

    company_plan = _build_company_plan(jobs, ctx, product_budget, product_progress)
    fit_plan = _build_fit_plan(jobs, ctx, fit_budget, fit_progress)

    product_stats = company_plan.stats if company_plan else _empty_company_stats()
    fit_stats = fit_plan.stats if fit_plan else _empty_fit_stats()

    if company_plan is None and fit_plan is None:
        return jobs, product_stats, fit_stats

    # tag is ("company", company_str) or ("fit", idx); populated incrementally so
    # the SIGINT handler always sees what's actually been submitted.
    futures: dict[Future[Any], tuple[str, Any]] = {}
    timeout = (
        company_plan.timeout
        if company_plan is not None
        else (
            fit_plan.timeout
            if fit_plan is not None
            else ctx.plugin.enrichment.enrich_timeout
        )
    )

    def _dispatch(fut: Future[Any]) -> None:
        # Route to the owning plan's consume, isolating non-interrupt errors so
        # one bad result (worker raise, non-finite fit) never escapes the loop,
        # skips the company stitch, and crashes run() with the batch lost.
        kind, key = futures[fut]
        if kind == "company":
            assert company_plan is not None
            _guarded_consume(
                company_plan.consume, fut, key, "company", company_plan.stats
            )
        else:
            assert fit_plan is not None
            _guarded_consume(fit_plan.consume, fut, key, "job", fit_plan.stats)

    # One shared executor caps total concurrency across both enrichers at
    # pool_size; both submit into it rather than spinning up a pool each. `with`
    # joins it on every exit path so a crash can't hang at atexit.
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        # ONE notifier for the whole overlapped section: nested per-enricher
        # installs would fight over signal.signal. "items" spans both nouns.
        previous_handler = _install_interrupt_notifier(futures, timeout, "items")
        # Interleave submission so the executor's queue carries both kinds from
        # the start — submitting all of one kind first would drain it before the
        # other ever ran, defeating the overlap.
        company_q = list(company_plan.companies) if company_plan is not None else []
        fit_q = list(fit_plan.target_idx) if fit_plan is not None else []
        ci = fi = 0
        while ci < len(company_q) or fi < len(fit_q):
            if ci < len(company_q):
                assert company_plan is not None
                futures[pool.submit(company_plan.fetch, company_q[ci])] = (
                    "company",
                    company_q[ci],
                )
                ci += 1
            if fi < len(fit_q):
                assert fit_plan is not None
                futures[pool.submit(fit_plan.fetch, fit_q[fi])] = ("fit", fit_q[fi])
                fi += 1

        try:
            for fut in as_completed(futures):
                _dispatch(fut)
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            for fut in futures:
                if fut.done() and not fut.cancelled():
                    # _dispatch already swallows non-interrupt errors, so KI
                    # always survives the drain to re-raise below.
                    _dispatch(fut)
            if company_plan is not None:
                company_plan.stitch()
            raise
        finally:
            _restore_interrupt_handler(previous_handler)

    if company_plan is not None:
        company_plan.stitch()
    log.info(
        "[enrich-fit-notes] done: %d enriched, %d failed, %d skipped (budget)",
        fit_stats["enriched"],
        fit_stats["failed"],
        fit_stats["skipped_budget"],
    )
    return jobs, product_stats, fit_stats
