"""Claude/LLM enricher: fit/notes (+criteria fold).

The enricher operates on ``EnrichedJob`` directly and fans out through one
pooled implementation: ``ThreadPoolExecutor(max_workers=pool_size)`` where
``pool_size == 1`` runs the work on the main thread (serial). Each replaces
slots in the passed list with new frozen instances and returns that list plus a
stats dict; the slots are replaced in the caller's own list (not a copy) so a
``KeyboardInterrupt`` mid-pass leaves enriched-so-far results in that list for
a caller that persists them — backfill rewrites on interrupt, and run() flushes
the sink per phase, every ~25 results, and on interrupt. Out-of-range LLM fit
scores are clamped at the model boundary with a logged warning rather than
silently stored.
"""

from __future__ import annotations

import json
import math
import shutil
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ThreadPoolExecutor,
    wait,
)
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


def _extract_json_payload(raw: str) -> str:
    """Peel LLM wrapping off a JSON response before parsing.

    Local models routinely fence their JSON in markdown (```json ... ```) or
    preface it with prose despite a strict-JSON prompt; a raw json.loads then
    fails the whole job. Strip a leading/trailing code fence, and if the result
    still doesn't start with a brace, fall back to the outermost {...} span.
    Returns the original string when no JSON-looking payload is found, so the
    caller's JSONDecodeError path (counted failure + warning) is unchanged.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop the opening fence line (``` or ```json) and a closing ``` line.
        body = lines[1:]
        if body and body[-1].strip().startswith("```"):
            body = body[:-1]
        text = "\n".join(body).strip()
    # Always trim to the outermost {...} span, not only when the text fails to
    # start with a brace: a valid object FOLLOWED by trailing prose (or a
    # mis-stripped fence) starts with "{" yet still breaks json.loads on the
    # extra data. The span is a no-op for a clean object.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return text or raw


def _location_summary(ctx: ScrapeContext) -> str:
    from daily_driver.plugins.job_search.scraper.countries import country_names
    from daily_driver.plugins.job_search.scraper.runner import home_city

    loc_cfg = ctx.plugin.locations
    # Spell out the SEMANTICS, not just the data: a bare "Countries: ..." list
    # next to "Based in: Vancouver" reads as candidate metadata, and models then
    # score any non-home-country job as a location mismatch (observed live with
    # a Spain role despite ES being configured). Say explicitly that a job in a
    # listed country IS acceptable.
    parts = [f"Based in: {home_city(ctx.plugin)}"]
    if loc_cfg is None:
        parts.append("remote roles are acceptable")
        return "; ".join(parts)
    if loc_cfg.countries:
        # Aliases are stored lowercase for matching; the longest is the full
        # country name (not the "usa"/"uk" abbrev). Title-case it for the prompt.
        names = [
            max(country_names(c), key=len, default=c).title() for c in loc_cfg.countries
        ]
        parts.append(
            "a job located in any of these countries is acceptable and is NOT a "
            "location mismatch: " + ", ".join(names)
        )
    if loc_cfg.remote:
        parts.append("remote roles are acceptable from anywhere")
    return "; ".join(parts)


# Criterion values that mean "nothing to surface" — folded results equal to one
# of these (case-insensitive) add no Notes noise, keeping the common case quiet.
_CRITERIA_SKIP_VALUES = {"unknown", "n/a"}

# Warn when an injected context.md exceeds this rough token estimate, since it
# rides every per-job enrichment prompt and multiplies cost across the run.
_CONTEXT_WARN_TOKENS = 1500

# How often the overlapped coordinator re-checks ctx.stop_event while waiting on
# in-flight LLM futures. Short enough that a cooperative stop returns promptly
# even when every future is mid-call, cheap enough to be a no-op on a normal run.
_OVERLAP_STOP_POLL_SECONDS = 0.1


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
    completion loop and crash run() with the whole scraped batch lost. Catching
    here turns it into one counted failure so the rest of the batch still
    enriches. ``KeyboardInterrupt`` always propagates.
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
    provider, model = ai_provider.resolve_route(
        ctx.ai, task="fit_notes", domain_cfg=enrichment_cfg
    )
    try:
        stdout = ai_provider.invoke_for(
            prompt,
            provider=provider,
            model=model,
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
        parsed = json.loads(_extract_json_payload(raw))
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
    return job.status not in ENRICH_SKIP_STATUSES and not (job.fit and job.notes)


@dataclass
class _FitPlan:
    """Resolved work for the fit/notes enricher.

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
    budget: int | None,
    progress: ProgressCallback | None,
    exclude_urls: frozenset[str] = frozenset(),
    on_planned: Callable[[int], None] | None = None,
) -> _FitPlan | None:
    """Resolve the fit/notes enricher's work, or ``None`` to skip the pass.

    Returns ``None`` when the claude CLI is missing or fit/notes are disabled in
    config — the caller then returns the untouched jobs with empty stats.

    ``exclude_urls`` are rows a prior wave already ATTEMPTED (enriched or failed);
    they are not retried here so a wave-1 failure is not re-charged against the
    shared budget in wave 2. Backfill is the retry path for those rows.
    """
    from daily_driver.plugins.job_search.scraper.runner import home_city

    stats = {"enriched": 0, "skipped_budget": 0, "failed": 0}
    # Replace slots in the caller's list (not a copy) so a KeyboardInterrupt
    # mid-pass leaves enriched-so-far results for a caller that persists them
    # (backfill rewrites on interrupt; run() flushes the sink per phase and
    # periodically, so partial results survive).
    out = jobs
    cfg = ctx.plugin.enrichment
    provider, _ = ai_provider.resolve_route(ctx.ai, task="fit_notes", domain_cfg=cfg)
    if provider == "claude" and shutil.which("claude") is None:
        log.warning("[enrich-fit-notes] claude CLI not found on PATH, skipping")
        return None

    if not cfg.enrich_fit or not cfg.enrich_notes:
        log.debug("[enrich-fit-notes] fit or notes disabled via config")
        return None

    # None means "use the config cap"; an explicit 0 means NO calls: a wave-2
    # whose shared budget wave 1 already exhausted must not re-grant the cap.
    if budget is None:
        budget = cfg.max_enrich_fit
    budget = max(0, budget)
    loc_summary = _location_summary(ctx)
    role_persona = ctx.plugin.persona or "SRE/Platform/Infra engineer"
    hc = home_city(ctx.plugin)
    crit_list = list(cfg.criteria)
    context_text = ctx.context_text
    timeout = cfg.enrich_timeout
    include_remote = cfg.enrich_is_remote

    pool_size = _enrich_pool_size(ctx)
    # Eligible indices preserve input order so budget truncation matches the
    # former first-N-by-position behavior. Rows a prior wave already attempted
    # are excluded by URL so they are neither retried nor re-charged.
    eligible_idx = [
        i
        for i, j in enumerate(out)
        if _fit_notes_eligible(j) and j.url not in exclude_urls
    ]
    eligible_count = len(eligible_idx)
    no_desc = sum(1 for i in eligible_idx if not out[i].description_text.strip())
    if context_text:
        # context.md rides EVERY eligible job's prompt -- _build_fit_notes_prompt
        # injects it whenever context is non-empty, regardless of whether the job
        # has a description, and no-description jobs are still in eligible_idx and
        # still make a full LLM call. So the projection counts every job that will
        # be enriched this run (capped by budget); subtracting no-desc jobs would
        # under-project the real token cost.
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
    # Report the PLANNED call count (post-budget) so the caller's progress bar
    # shows the real denominator, not the whole eligible count.
    if on_planned is not None:
        on_planned(len(target_idx))
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
            # INFO (visible at -v): a one-liner naming the failed job so a single
            # failure is identifiable without dropping to -vv. The provider-level
            # cause was already logged at WARNING by the fetch worker.
            log.info("[enrich-fit-notes] %s: enrichment failed, no write", company)
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
        if not updates:
            # No cell changed (e.g. a pre-existing fit with an empty-notes reply):
            # count nothing rather than inflate the enriched total.
            return
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
    budget: int | None = None,
    progress: ProgressCallback | None = None,
    flush: Callable[[], None] | None = None,
    flush_every: int = 25,
    _reset_hint: bool = True,
    exclude_urls: frozenset[str] = frozenset(),
    attempted: dict[str, set[str]] | None = None,
    on_planned: Callable[[int], None] | None = None,
) -> tuple[list[EnrichedJob], dict[str, int]]:
    """Populate Fit score and Notes for new jobs via one provider call per job.

    One call per job returns strict JSON ``{"fit": <int 1-10>, "notes": "..."}``.
    Budget caps the combined call count (uses ``max_enrich_fit`` as the limit).
    Fit is scored from role/company/location alone, so description is optional;
    when absent, notes is left empty (not confabulated). Fans out through
    ``ThreadPoolExecutor`` when ``max_parallel > 1``; ``pool_size == 1`` runs
    serially on the main thread.

    ``flush`` (with ``flush_every``) is the resilience hook: composed onto the
    progress callback so it runs on the single coordinator thread that applies
    results every ``flush_every`` applied results, never racing a worker. The
    caller flushes again per completed phase and on interrupt.

    ``_reset_hint`` re-arms the once-per-run ollama queue hint.

    ``exclude_urls`` are rows a prior wave attempted (not retried); ``attempted``
    (out-param) records this pass's attempted row URLs under ``"fit_urls"`` for
    cross-wave budget accounting.

    Returns ``(jobs, stats)`` with stats keys: enriched, skipped_budget, failed.
    """
    if _reset_hint:
        _reset_ollama_hint()
    # Compose the periodic flush onto the progress callback; the per-result
    # signal already runs on the single coordinator thread that applies results,
    # so wrapping it keeps the rewrite off the worker threads.
    applied = [0]
    progress = _wrap_progress_with_flush(progress, flush, flush_every, applied)
    plan = _build_fit_plan(
        jobs, ctx, budget, progress, exclude_urls, on_planned=on_planned
    )
    if attempted is not None:
        attempted["fit_urls"] = (
            {jobs[i].url for i in plan.target_idx} if plan else set()
        )
    if plan is None:
        return jobs, _empty_fit_stats()

    pool_size = _enrich_pool_size(ctx)
    if pool_size > 1:
        # `with` joins the pool on every exit path so a crash can't hang at atexit.
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            # Populate `futures` incrementally so the SIGINT handler always sees a
            # mapping that reflects what's been submitted (a bulk replace would
            # leave a window where the handler reads {} and reports "0").
            futures: dict[Any, int] = {}
            previous_handler = _install_interrupt_notifier(
                futures, plan.timeout, "jobs"
            )
            for idx in plan.target_idx:
                futures[pool.submit(plan.fetch, idx)] = idx

            def _drain_settled() -> None:
                # Cancel pending work and apply the results already in hand.
                # Shared by the KeyboardInterrupt path and the cooperative
                # stop_event path so both quiesce identically.
                pool.shutdown(wait=False, cancel_futures=True)
                for fut in futures:
                    if fut.done() and not fut.cancelled():
                        # _guarded_consume swallows non-interrupt errors, so a KI
                        # in a settled future still survives the drain to re-raise.
                        _guarded_consume(
                            plan.consume, fut, futures[fut], "job", plan.stats
                        )

            try:
                # Poll with a bounded wait rather than a bare as_completed loop so
                # the cooperative stop_event is honored even while every in-flight
                # future is still blocked (a long LLM call): as_completed would not
                # yield -- and so never re-check the event -- until the next
                # completion.
                pending: set[Future[Any]] = set(futures)
                while pending:
                    # Cooperative stop: an interrupt during the scrape/enrich
                    # overlap sets ctx.stop_event on the MAIN thread. A child
                    # thread never receives the KeyboardInterrupt, so without this
                    # check the loop would run every submitted future to completion
                    # (spending the full remaining LLM budget) before the caller's
                    # bounded join gives up. On stop, drain what settled and return
                    # -- making that join a real drain. The normal path (event
                    # never set) applies every completion exactly as a plain
                    # as_completed loop would.
                    if ctx.stop_event.is_set():
                        _drain_settled()
                        return plan.out, plan.stats
                    done, pending = wait(
                        pending,
                        timeout=_OVERLAP_STOP_POLL_SECONDS,
                        return_when=FIRST_COMPLETED,
                    )
                    for fut in done:
                        _guarded_consume(
                            plan.consume, fut, futures[fut], "job", plan.stats
                        )
            except KeyboardInterrupt:
                _drain_settled()
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

    # Hold every settled Future referenced for the loop's lifetime: consume()
    # de-dupes on id(fut), and a GC'd-then-recycled id would make a later result
    # look already-consumed and silently drop it.
    settled: list[Future[Any]] = []
    for idx in plan.target_idx:
        # Cooperative stop, mirroring the parallel branch: during the
        # scrape/enrich overlap this serial loop runs on the wave-1 background
        # thread, which never receives the KeyboardInterrupt -- ctx.stop_event is
        # its only stop signal. Without this check a provider=ollama,
        # max_parallel=1 run would keep issuing per-job LLM calls for the full
        # remaining budget after the user pressed Ctrl-C. Results applied so far
        # stay in plan.out for the caller's flush.
        if ctx.stop_event.is_set():
            break
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


def _empty_fit_stats() -> dict[str, int]:
    return {"enriched": 0, "skipped_budget": 0, "failed": 0}


def _wrap_progress_with_flush(
    progress: ProgressCallback | None,
    flush: Callable[[], None] | None,
    flush_every: int,
    applied: list[int],
) -> ProgressCallback | None:
    """Compose a periodic flush onto a per-enricher progress callback.

    Every applied result (the existing ``progress(1, name)`` signal) bumps the
    shared ``applied`` counter; once it crosses a ``flush_every`` boundary the
    flush fires. Wrapping the progress callback keeps the flush on the single
    coordinator thread that already applies results -- the rewrite never races a
    worker. Returns the original callback unchanged when no flush is configured.
    """
    if flush is None or flush_every <= 0:
        return progress

    def _wrapped(n: int = 1, detail: str | None = None) -> None:
        if progress is not None:
            progress(n, detail)
        applied[0] += n
        if applied[0] % flush_every == 0:
            flush()

    return _wrapped
