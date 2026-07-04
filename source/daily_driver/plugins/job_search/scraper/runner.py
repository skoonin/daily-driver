"""Scraper orchestration: run() / run_backfill() and the enrichment waves.

Row-level dedup/lift helpers live in :mod:`rows`; the ScrapeContext and its
exceptions in :mod:`context`; the per-run durable sink in :mod:`sink`; the
scraper fan-out (``run_all_scrapers`` and friends) in :mod:`scrape_all`. This
module keeps the public entry points and the enrichment-wave plumbing, and
imports from the split modules everything its remaining code calls.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from daily_driver.core.clock import today
from daily_driver.core.config_models import AIConfig
from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import (
    get_logger,
    live_log_window,
)
from daily_driver.core.progress import Group, Item, Phase, RunProgress
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.jobs_lock import (
    LOCK_GIVEUP_MESSAGE,
    LOCK_WAIT_TIMEOUT_SECONDS,
    clear_stale_adjacent_lock,
    jobs_lock_path,
    workspace_busy_notice,
)
from daily_driver.plugins.job_search.scraper.context import (
    CheckpointAborted,
    ScrapeContext,
    ScraperError,
)
from daily_driver.plugins.job_search.scraper.enrichment.llm import _empty_fit_stats
from daily_driver.plugins.job_search.scraper.models import (
    ENRICH_ELIGIBLE_STATUSES,
    SaturationRecord,
)
from daily_driver.plugins.job_search.scraper.rows import (
    _enriched_from_scraped,
    dedup_key,
    location_matches,
)
from daily_driver.plugins.job_search.scraper.scrape_all import (
    _display_name,
    _fmt_duration,
    _funnel_other,
    _per_source_funnel,
    _slow_source_note,
    _source_breakdown_segments,
    run_all_scrapers,
)
from daily_driver.plugins.job_search.scraper.sink import _JobSink

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob

log = get_logger(__name__)


# Remediation hint per (source, kind) for the run-summary Saturated line.
# Per-source because each has its own limit knob; workday's page ceiling is a
# code constant, not config.
_SATURATION_HINTS: dict[tuple[str, str], str] = {
    ("linkedin", "cap"): (
        "results capped; raise results_wanted_per_query or split terms"
    ),
    ("indeed", "cap"): (
        "results capped; raise results_wanted_per_query or split terms"
    ),
    ("linkedin", "plateau"): (
        "stopped early at/past the ~100/IP rate-limit zone; "
        "deeper coverage needs proxies"
    ),
    ("workday", "cap"): "page ceiling hit; some postings unfetched",
    ("apple", "cap"): "scroll cap hit; raise scraper.max_pages",
}


# ── Ollama enrichment preflight ──────────────────────────────────────────────


# Independent of the configured per-call enrich_timeout: this single reachability
# probe should fail fast on a down server, not wait out a tuning value meant for
# real generation calls.
_OLLAMA_PREFLIGHT_TIMEOUT = 3

# Bound on the interrupt-path join of the overlap (wave-1) enrichment thread.
# Long enough to let an in-flight LLM call settle and flush, short enough that a
# second Ctrl-C is not swallowed; past it the daemon thread expires on its own
# enrich_timeout and the final flush proceeds with what it applied.
_WAVE1_INTERRUPT_JOIN_SECONDS = 30


def _llm_enrichment_requested(plugin: JobSearchPlugin) -> bool:
    """Whether this run will make ollama LLM calls (detail pages don't count).

    Detail-page enrichment is plain HTTP against the job board, so it needs no
    provider. Only the fit / notes pass routes to the LLM, so the preflight is
    warranted only when it is on.
    """
    cfg = plugin.enrichment
    return cfg.enrich_fit or cfg.enrich_notes


def _ollama_enrichment_preflight(plugin: JobSearchPlugin, ai: AIConfig) -> bool:
    """Ping ollama once before LLM enrichment; return False (and warn) to skip.

    Mirrors the claude which-guard intent — verify the provider is usable before
    the per-job loop — but for ollama the check is a network probe, so it runs
    here at run start rather than per enricher. One cheap ``list_models`` GET on
    a short fixed timeout (:data:`_OLLAMA_PREFLIGHT_TIMEOUT`) stands in for N
    per-call timeouts on a down server. Reuses the doctor reachability + pulled-
    model logic.

    Each enrichment phase routes independently (a global `ai.provider: ollama`
    reaches enrichment even with the enrichment block unset), so the resolved
    route per phase — not the raw `enrichment.provider` — decides whether to
    probe. Returns True (proceed) when no enabled phase resolves to ollama.
    """
    from daily_driver.integrations import ai_provider, ollama_client

    cfg = plugin.enrichment
    # A phase is probed only when its own toggles are on AND it resolves to
    # ollama: a disabled phase makes no LLM calls, so its route is moot.
    phase_enabled = {
        "fit_notes": cfg.enrich_fit or cfg.enrich_notes,
    }
    ollama_models: list[str] = []
    for phase, enabled in phase_enabled.items():
        if not enabled:
            continue
        provider, model = ai_provider.resolve_route(ai, task=phase, domain_cfg=cfg)
        if provider == "ollama":
            ollama_models.append(model or ollama_client.DEFAULT_MODEL)
    if not ollama_models:
        return True

    endpoint = ai.ollama.endpoint
    try:
        pulled = ollama_client.list_models(endpoint, timeout=_OLLAMA_PREFLIGHT_TIMEOUT)
    except ollama_client.OllamaNotReachableError:
        Console.warning(
            f"ollama not reachable at {endpoint} -- LLM enrichment skipped this "
            "run; start the server (ollama serve) and run jobs backfill to "
            "fill these rows"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        Console.warning(
            f"ollama at {endpoint} returned an error ({exc}) -- LLM enrichment "
            "skipped this run; run jobs backfill once it is healthy"
        )
        return False

    # Preserve insertion order while de-duping (both phases may share a model).
    missing = [m for m in dict.fromkeys(ollama_models) if m not in pulled]
    if missing:
        model = missing[0]
        Console.warning(
            f"ollama model {model!r} not pulled at {endpoint} -- LLM enrichment "
            f"skipped this run; pull it (ollama pull {model}) and run jobs "
            "backfill to fill these rows"
        )
        return False
    return True


# ── Public entry points ──────────────────────────────────────────────────────


def _backfill_needs(
    jobs: list[EnrichedJob],  # noqa: F821
    plugin: JobSearchPlugin,
    force: bool = False,
    cooldown_cutoff: datetime | None = None,
) -> dict[str, int]:
    """Per-phase counts of rows the enricher WOULD actually touch.

    Rows in an ENRICH_SKIP status are excluded (deliberately untouched). The
    counts mirror the enricher's own eligibility, not a re-implementation:

    - ``fit_notes`` uses :func:`_fit_notes_eligible` -- the combined predicate,
      since one fit/notes call fills both Fit and Notes. There is no separate
      notes count (a single call covers it), so the dry-run report and the
      nothing-to-do short-circuit speak in those terms. The count is gated on
      BOTH ``enrich_fit`` and ``enrich_notes``: the fit pass refuses to run
      unless both are on, so a disabled axis reports zero need rather than
      over-reporting work the pass would never do. Under ``force`` every active
      row counts (the enricher re-enriches and overwrites all of them).

    Used by the dry-run report and the start-of-run short-circuit.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import _active
    from daily_driver.plugins.job_search.scraper.enrichment.llm import (
        _fit_notes_eligible,
    )

    cfg = plugin.enrichment
    active = [j for j in jobs if _active(j)]
    fit_notes = (
        sum(
            1
            for j in active
            if _fit_notes_eligible(j, force=force, cooldown_cutoff=cooldown_cutoff)
        )
        if (cfg.enrich_fit and cfg.enrich_notes)
        else 0
    )
    return {
        "rows": len(active),
        "fit_notes": fit_notes,
    }


def run_backfill(
    plugin: JobSearchPlugin,
    csv_path: Path,
    ephemeral_dir: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
    dry_run: bool = False,
    limit: int | None = None,
    force: bool = False,
    cooldown_hours: int | str | None = None,
    emit_json: bool = False,
) -> dict[str, Any]:
    """Re-enrich empty fields in an existing jobs.csv via the modern driver.

    Shares the run-side enrichment machinery: ``_enrich_wave`` renders the same
    Detail pages / Fit and notes phase rows under a "Job backfill"
    :class:`RunProgress` block, fans the fit/notes pass out under the provider's
    concurrency cap, flushes every ~25 results, and runs the ollama preflight
    before the LLM phase.

    The enricher itself skips already-filled fields and ENRICH_SKIP-status
    rows, so handing the whole row list to the wave enriches only the empties.
    ``force`` (``--force-update``) instead re-enriches EVERY active row and
    OVERWRITES its Fit, Notes, and Remote, still bounded by ``limit`` and the
    fit budget cap.

    ``limit`` caps LLM spend this invocation by bounding the fit budget at
    ``limit`` (``None`` = the config cap; the CLI rejects ``limit < 1``).
    ``dry_run`` reports the would-enrich count and makes zero LLM/detail calls
    and zero writes (no backup either).

    Locking: the sentinel ``file_lock`` is held for the WHOLE lifecycle -- the
    read of jobs.csv, the enrichment, and every rewrite all happen inside it.
    backfill rewrites the file from its in-memory snapshot, so reading or
    rewriting outside the lock would let a concurrent run's append slip into the
    window and be clobbered. The sink is built with ``external_lock_held=True``
    so its per-flush sections do not re-take the (non-reentrant) flock.

    The pre-mutation backup is LAZY: taken just before the first actual write
    (the sink's ``pre_write_hook``), so a no-op backfill (ollama down, nothing to
    persist) leaves the file untouched and writes no backup. The final rewrite is
    skipped entirely when enrichment changed no row content.

    Returns a completion summary dict (rows considered, skipped, Fit/Notes
    needed-before/after, enriched delta, elapsed seconds) so the CLI can wrap it
    in the standard ``{schema, data}`` JSON envelope. ``emit_json`` suppresses
    the human Console summary lines and forces plain (no live-bar) progress so
    ``--json`` keeps stdout clean.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        _make_backup,
        format_canonicalized_notice,
        read_rows,
    )
    from daily_driver.plugins.job_search.scraper.descriptions import (
        gc_descriptions,
        load_descriptions,
    )
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob

    if not csv_path.exists():
        raise ScraperError(f"jobs.csv not found at {csv_path}")

    backfill_started_at = datetime.now(timezone.utc)
    ctx = ScrapeContext(plugin=plugin, ai=ai or AIConfig(), context_text=context_text)
    ai_cfg = ctx.ai
    clear_stale_adjacent_lock(csv_path)
    lock_path = jobs_lock_path(ephemeral_dir)

    # Force-update cooldown: skip rows enriched within the window so an
    # interrupted force-update resumes instead of restarting. CLI --cooldown-hours
    # overrides the config default; the cutoff is only meaningful under force.
    # `0` disables it (re-enrich every active row); `missing` re-enriches only
    # rows with no enrichment timestamp yet -- modelled as a cutoff before any
    # real timestamp, so every already-enriched row falls "within cooldown".
    resolved_cooldown: int | str = (
        cooldown_hours
        if cooldown_hours is not None
        else plugin.enrichment.force_recook_cooldown_hours
    )
    cooldown_cutoff: datetime | None
    if not force:
        cooldown_cutoff = None
    elif resolved_cooldown == "missing":
        cooldown_cutoff = datetime.min.replace(tzinfo=timezone.utc)
    elif isinstance(resolved_cooldown, int) and resolved_cooldown > 0:
        cooldown_cutoff = backfill_started_at - timedelta(hours=resolved_cooldown)
    else:  # 0 (or unexpected) -> cooldown disabled
        cooldown_cutoff = None

    # Dry-run reads outside the lock: it never writes, so a concurrent run can't
    # be clobbered by it. The real path reads INSIDE the lock (below).
    if dry_run:
        _, stored_rows = read_rows(csv_path)
        jobs = [EnrichedJob.from_csv_row(r) for r in stored_rows]
        store = load_descriptions(csv_path)
        jobs = [
            (
                job.with_updates(description_text=store[job.url])
                if not job.description_text and job.url in store
                else job
            )
            for job in jobs
        ]
        needs = _backfill_needs(
            jobs, plugin, force=force, cooldown_cutoff=cooldown_cutoff
        )
        skipped = len(jobs) - needs["rows"]
        enrich_cfg = plugin.enrichment
        fit_cap = limit if limit is not None else enrich_cfg.max_enrich_fit
        # Preview the sidecar GC without writing (dry-run mutates nothing).
        live_urls = {job.url for job in jobs if job.url}
        would_drop = sum(1 for url in store if url not in live_urls)
        if not emit_json:
            if would_drop:
                Console.info(
                    f"Backfill dry-run: would clean up {would_drop} orphaned "
                    "description(s). Nothing written."
                )
            verb = "re-enrich (overwrite)" if force else "need"
            Console.info(
                f"Backfill dry-run: {needs['fit_notes']} {verb} Fit/Notes "
                f"({needs['rows']} active rows, {skipped} skipped). Nothing written."
            )
            # The needs counts are the FULL backlog; one pass spends at most the
            # config caps (or --limit). Say so when a cap would trim this pass,
            # or the report reads as if the caps were ignored.
            if needs["fit_notes"] > fit_cap:
                source = "--limit" if limit is not None else "config"
                Console.info(
                    f"This pass would be fit/notes capped at {fit_cap} ({source}); "
                    "run backfill again to continue."
                )
        return {
            "dry_run": True,
            "rows": needs["rows"],
            "skipped": skipped,
            "needs_before": needs["fit_notes"],
            "needs_after": needs["fit_notes"],
            "enriched": 0,
            "fit_cap": fit_cap,
            "elapsed_seconds": None,
        }

    # No --limit: None lets the enrichment plan apply the config cap
    # (max_enrich_fit).
    fit_budget = limit

    tty = Console.live_progress_enabled(suppress=emit_json)
    # Announce-then-wait on contention, with a bounded give-up so a wedged peer
    # can't hang backfill forever. ExitStack lets the give-up branch return
    # before the lock body without re-indenting the whole lifecycle block.
    lock_stack = ExitStack()
    try:
        lock_stack.enter_context(
            file_lock(
                lock_path,
                timeout=LOCK_WAIT_TIMEOUT_SECONDS,
                on_contention=workspace_busy_notice,
            )
        )
    except TimeoutError:
        raise ScraperError(LOCK_GIVEUP_MESSAGE) from None
    try:
        # READ INSIDE THE LOCK: the snapshot the rewrite is built from must be
        # consistent with the lock window, or a concurrent run's append between
        # an out-of-lock read and the rewrite would be lost.
        stored_header, stored_rows = read_rows(csv_path)
        jobs = [EnrichedJob.from_csv_row(r) for r in stored_rows]
        store = load_descriptions(csv_path)
        jobs = [
            (
                job.with_updates(description_text=store[job.url])
                if not job.description_text and job.url in store
                else job
            )
            for job in jobs
        ]
        # Reconcile the description sidecar against the live jobs.csv URLs read
        # above (archived rows never need descriptions -- the store is a pure
        # cache). Prune the in-memory store to match too, or the sink's later
        # dirty flush would re-persist the orphans this GC just dropped.
        live_urls = {job.url for job in jobs if job.url}
        dropped_descriptions = gc_descriptions(csv_path, live_urls, store=store)
        if dropped_descriptions:
            store = {url: text for url, text in store.items() if url in live_urls}
            if not emit_json:
                Console.info(
                    f"Cleaned up {dropped_descriptions} orphaned description(s)."
                )
        # Backfill lifts every stored row through from_csv_row, which normalizes
        # the Status spelling. That rewrites rows the user did not ask to touch,
        # so collect the spelling changes (old -> canonical) for a visible
        # notice once enrichment finishes. Meaning is never changed.
        status_canonicalizations: list[tuple[str, str]] = [
            (raw, job.status)
            for raw, job in (
                ((r.get("Status") or ""), j) for r, j in zip(stored_rows, jobs)
            )
            if raw and raw != job.status
        ]
        # Unknown / hand-added columns (e.g. a user's "Priority") are not modelled
        # by EnrichedJob; carry them through POSITIONALLY (the sink merges
        # row_extras by index, lossless for empty/duplicate Links). The sink gets
        # CANONICAL_HEADER plus the explicit extra-column ORDER from the file.
        extra_columns = [c for c in stored_header if c not in CANONICAL_HEADER]
        row_extras = [{c: r.get(c, "") for c in extra_columns} for r in stored_rows]
        if extra_columns:
            log.info(
                "[backfill] carrying through %d non-canonical column(s): %s",
                len(extra_columns),
                ", ".join(extra_columns),
            )

        needs = _backfill_needs(
            jobs, plugin, force=force, cooldown_cutoff=cooldown_cutoff
        )
        skipped = len(jobs) - needs["rows"]
        log.info(
            "[backfill] %d rows (%d skipped excluded): %d need Fit/Notes",
            len(jobs),
            skipped,
            needs["fit_notes"],
        )

        if not needs["fit_notes"]:
            # No backup, no rewrite: release the lock cleanly. (The lock context
            # exits normally when we return.)
            if not emit_json:
                Console.info("All rows already enriched, nothing to backfill.")
            elapsed = (datetime.now(timezone.utc) - backfill_started_at).total_seconds()
            return {
                "dry_run": False,
                "rows": len(jobs),
                "skipped": skipped,
                "needs_before": 0,
                "needs_after": 0,
                "enriched": 0,
                "elapsed_seconds": elapsed,
            }

        sink = _JobSink(
            csv_path=csv_path,
            lock_path=lock_path,
            header=CANONICAL_HEADER,
            known_urls=set(),
            known_keys=set(),
            plugin=plugin,
            external_lock_held=True,
            descriptions=store,
        )
        sink.rows = jobs
        sink.row_extras = row_extras
        sink.extra_columns = extra_columns
        # No-churn: every backfill flush (periodic and final) skips the write --
        # and the lazy backup -- when nothing actually changed on disk.
        sink.skip_flush_if_unchanged = True

        # Lazy backup: the sink fires this once, just before its first real write.
        backup_holder: list[Path] = []

        def _take_backup() -> None:
            backup = _make_backup(csv_path)
            backup_holder.append(backup)
            log.info("[backfill] backed up to %s", backup.name)

        sink.pre_write_hook = _take_backup

        # Captured from the enrichment wave below; reported after completion.
        fit_stats: dict[str, int] = _empty_fit_stats()

        try:
            with (
                live_log_window(tty),
                RunProgress(
                    Console.get_log_console(), tty=tty, title="Job backfill"
                ) as rp,
            ):
                # title + "Enriching jobs" header + the phase rows the wave will
                # render -- reserve the block in one scroll-region set, as run()
                # does, to avoid the per-bar resize gap. Phases: detail (always)
                # and fit/notes (when enabled). Must match the fit_enabled gate in
                # _enrich_wave or the reserved row count drifts from the phases
                # actually rendered.
                enrich_cfg = plugin.enrichment
                phase_rows = 1
                if enrich_cfg.enrich_fit and enrich_cfg.enrich_notes:
                    phase_rows += 1
                rp.reserve(2 + phase_rows)
                enrich_group = rp.group("Enriching jobs")
                _, fit_stats = _enrich_wave(
                    plugin,
                    ai_cfg,
                    jobs,
                    ctx,
                    sink,
                    enrich_group,
                    wave_label="",
                    run_preflight=True,
                    fit_budget=fit_budget,
                    force=force,
                    cooldown_cutoff=cooldown_cutoff,
                    capture_descriptions=False,
                )
                enrich_group.done()
        except KeyboardInterrupt as interrupt:
            # Persist whatever the interrupted wave produced; the final rewrite
            # still runs under the held lock. The original jobs.csv is intact if
            # the write itself fails (atomic rename only fires on success), and a
            # backup gives a clean rollback either way. A second Ctrl-C (or any
            # other error) during this teardown flush must NOT replace the
            # original interrupt and degrade exit 130 -> 1: log it and re-raise
            # the original. The lazy backup is taken INSIDE flush, so read
            # backup_holder AFTER it returns.
            try:
                sink.flush()
            except KeyboardInterrupt:
                log.warning("Second interrupt during backfill teardown flush")
            except Exception as exc:  # noqa: BLE001 -- never mask the original KI
                log.error("Backfill teardown flush failed: %s", exc)
            else:
                if backup_holder:
                    Console.warning(
                        f"Backfill interrupted: partial progress saved to "
                        f"jobs.csv (original preserved at {backup_holder[0].name})"
                    )
                else:
                    Console.warning("Backfill interrupted: no changes to save")
            raise interrupt
        else:
            # Parity with run(): if periodic saves degraded mid-run, say so once,
            # then let the final flush retry. The final flush is a no-op (no
            # write, no backup) when enrichment changed nothing -- e.g. ollama
            # down -- so a quiet run leaves the file's bytes and mtime untouched.
            # A final-flush OSError is caught here to name the backup before
            # re-raising, so the user has a rollback path even when the disk
            # write fails.
            if sink.persistence_degraded:
                Console.warning(
                    f"periodic saves failed during the backfill "
                    f"({sink._degraded_reason}); retrying the final save now"
                )
            try:
                sink.flush()
            except OSError as exc:
                backup_name = backup_holder[0].name if backup_holder else "(none taken)"
                Console.error(
                    f"Backfill final save failed ({exc}); original preserved "
                    f"at {backup_name}"
                )
                raise
    finally:
        lock_stack.close()

    after = _backfill_needs(jobs, plugin)
    elapsed = (datetime.now(timezone.utc) - backfill_started_at).total_seconds()
    enriched = needs["fit_notes"] - after["fit_notes"]
    if not emit_json:
        Console.success(
            f"Backfill complete: +{enriched} Fit/Notes. "
            f"Total run time: {_fmt_duration(elapsed)}."
        )
        # Backfill relies solely on the cached descriptions (descriptions.jsonl):
        # it never fetches over the network. A row with no cached description is
        # left un-scored (and gets no enrichment timestamp) rather than guessed
        # at; call it out so the phase-line count isn't mistaken for ordinary
        # skips. Descriptions are captured at scrape time; a row already in
        # jobs.csv is deduped by a re-run, so recovering it means deleting it so
        # the next scrape re-adds (and describes) it.
        no_description_count = fit_stats.get("no_description", 0)
        if no_description_count:
            Console.warning(
                f"{no_description_count} job(s) had no cached description; "
                "Fit/Notes left blank. Descriptions are captured during "
                "`jobs run`; delete a row so a re-scrape re-adds and describes it."
            )
        # The rewrite ran (this point is reached only past the no-enrichment early
        # return), so any status spellings it canonicalized are now on disk. Make
        # that visible, once, when something actually changed.
        canon_notice = format_canonicalized_notice(status_canonicalizations)
        if canon_notice is not None:
            Console.info(canon_notice)
    return {
        "dry_run": False,
        "rows": len(jobs),
        "skipped": skipped,
        "needs_before": needs["fit_notes"],
        "needs_after": after["fit_notes"],
        "enriched": enriched,
        "elapsed_seconds": elapsed,
    }


def _print_dry_run_table(jobs: list[EnrichedJob]) -> None:  # noqa: F821
    """Render a Rich table summary of dry-run matches."""
    from rich.console import Console
    from rich.table import Table

    from daily_driver.plugins.job_search.scraper.parsing import _fix_mojibake

    console = Console(stderr=False)
    if not jobs:
        console.print("[dim]Dry-run: no new jobs matched.[/dim]")
        return

    table = Table(
        title=f"Dry-run preview ({len(jobs)} new jobs)",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Source")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Location")
    table.add_column("URL", overflow="fold")
    for j in jobs:
        table.add_row(
            _fix_mojibake(j.source),
            _fix_mojibake(j.company),
            _fix_mojibake(j.role),
            _fix_mojibake(j.location),
            j.url,
        )
    console.print(table)
    console.print(
        f"[dim]{len(jobs)} new jobs (dry-run, nothing written).[/dim]",
    )


def _run_llm_enrichment(
    plugin: JobSearchPlugin,
    ai_cfg: AIConfig,
    typed_jobs: list[EnrichedJob],  # noqa: F821
    ctx: ScrapeContext,
    sink: _JobSink,
    fit_phase: Phase | None,
    *,
    run_preflight: bool,
    fit_budget: int | None = None,
    exclude_fit_urls: frozenset[str] = frozenset(),
    attempted: dict[str, set[str]] | None = None,
    force: bool = False,
    cooldown_cutoff: datetime | None = None,
) -> dict[str, int]:
    """Run the fit/notes LLM pass, flushing as it goes.

    Returns ``fn_stats``. ``run_preflight`` gates the ollama reachability probe
    -- run it before the FIRST wave only; later waves skip it (the server state
    cannot change mid-run). On a failed probe the LLM phase renders as a
    zero-count done bar and zero counters (the same shape ``--no-enrich``
    records), so the manifest stays honest and the user fills the rows later with
    ``jobs backfill``. The claude provider is untouched (its which-guard inside
    the enricher already covers a missing CLI). ``sink.flush_periodic`` is the
    periodic resilience hook, invoked every ~25 results on the coordinator
    thread. ``fit_budget`` is the SHARED running total's remaining allowance for
    this wave (None = the config cap; an explicit 0 spends nothing).
    """
    from daily_driver.plugins.job_search.scraper.enrichment import (
        enrich_fit_and_notes,
    )

    run_llm = True
    if run_preflight and _llm_enrichment_requested(plugin):
        run_llm = _ollama_enrichment_preflight(plugin, ai_cfg)

    if run_llm:
        # A None phase means the pass is disabled by config: no bar exists and
        # the plan builder skips the pass on its own toggles.
        if fit_phase is not None:
            fit_phase.start()
        _, fn_stats = enrich_fit_and_notes(
            typed_jobs,
            ctx,
            budget=fit_budget,
            progress=fit_phase.advance if fit_phase is not None else None,
            # The bar is pinned with total=len(jobs) before the plan exists; once
            # the planner resolves the budget-capped call count, re-base the
            # denominator so the bar shows what will actually be spent.
            on_planned=fit_phase.set_total if fit_phase is not None else None,
            # Periodic in-loop flush degrades (not crashes) on a disk error.
            flush=sink.flush_periodic,
            exclude_urls=exclude_fit_urls,
            attempted=attempted,
            force=force,
            cooldown_cutoff=cooldown_cutoff,
        )
    else:
        fn_stats = _empty_fit_stats()
    if fit_phase is not None:
        # .get() rather than a bare index: callers (tests, older stubs) may
        # return a stats dict without this key, and 0 is the correct default.
        fit_phase.done(
            f"{fn_stats['enriched']} enriched, "
            f"{fn_stats['skipped_budget']} skipped (budget), "
            f"{fn_stats.get('no_description', 0)} no description, "
            f"{fn_stats['failed']} failed"
        )
    return fn_stats


def _enrich_wave(
    plugin: JobSearchPlugin,
    ai_cfg: AIConfig,
    jobs: list[EnrichedJob],  # noqa: F821
    ctx: ScrapeContext,
    sink: _JobSink,
    enrich_group: Group,
    *,
    wave_label: str,
    run_preflight: bool,
    fit_budget: int | None,
    set_phase: Callable[[str], None] | None = None,
    exclude_fit_urls: frozenset[str] = frozenset(),
    attempted: dict[str, set[str]] | None = None,
    force: bool = False,
    cooldown_cutoff: datetime | None = None,
    capture_descriptions: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Run one enrichment wave (detail + LLM) over ``jobs`` in place.

    ``wave_label`` suffixes the phase rows ("" for the only/first wave, e.g.
    " (wave 2)" for the post-Apple wave) so the live display shows each wave's
    own phase rows -- the honest two-wave UI. ``fit_budget`` is this wave's
    remaining slice of the shared running total (None = the config cap; an
    explicit 0 spends nothing). ``set_phase`` records the coarse run phase for
    the manifest. ``force`` re-enriches and overwrites every active row's
    Fit/Notes/Remote (backfill --force-update). ``capture_descriptions`` False
    (the backfill path) keeps descriptions cache-only: the detail phase fills
    comp but not description_text, and a row with no cached description is left
    un-enriched by fit/notes rather than fetched. Flushes per phase and on the
    periodic hook; the caller's try/except flushes once more on interrupt.
    Returns ``(detail_stats, fn_stats)``.
    """
    from daily_driver.plugins.job_search.scraper.enrichment import enrich_job_details
    from daily_driver.plugins.job_search.scraper.enrichment.detail import (
        render_detail_summary,
    )

    total = len(jobs)
    detail_phase = enrich_group.phase(f"Detail pages{wave_label}", total=total)
    # A disabled pass renders NO bar: pinning one with a placeholder total for
    # work that will never run reads as a stuck/ignored toggle.
    enrich_cfg = plugin.enrichment
    fit_enabled = enrich_cfg.enrich_fit and enrich_cfg.enrich_notes
    fit_phase: Phase | None = None
    if fit_enabled:
        fit_phase = enrich_group.phase(f"Fit and notes{wave_label}", total=total)

    if set_phase is not None:
        set_phase("detail")
    detail_phase.start()
    _, detail_stats = enrich_job_details(
        jobs,
        ctx,
        progress=detail_phase.advance,
        capture_descriptions=capture_descriptions,
    )
    detail_phase.done(render_detail_summary(detail_stats))
    # Persist detail comp/posted_date before the LLM phase; a disk error here
    # degrades rather than aborting (the final flush retries and propagates).
    sink.flush_periodic()

    if set_phase is not None:
        set_phase("enrichment")

    fn_stats = _run_llm_enrichment(
        plugin,
        ai_cfg,
        jobs,
        ctx,
        sink,
        fit_phase,
        run_preflight=run_preflight,
        fit_budget=fit_budget,
        exclude_fit_urls=exclude_fit_urls,
        attempted=attempted,
        force=force,
        cooldown_cutoff=cooldown_cutoff,
    )
    return detail_stats, fn_stats


def _add_stats(into: dict[str, int], more: dict[str, int]) -> None:
    """Accumulate one enrichment stats dict into a running total in place.

    Two-wave runs sum each wave's counters so the end-of-run reconciliation and
    manifest report the combined total, not the last wave alone.
    """
    for key, value in more.items():
        into[key] = into.get(key, 0) + value


def _accumulate_enrich_stats(
    fn_stats: dict[str, int],
    wave_result: dict[str, dict[str, Any]],
) -> None:
    """Fold a completed wave's fit stats into the running total."""
    if "fit" in wave_result:
        _add_stats(fn_stats, wave_result["fit"])


@dataclass
class _RunState:
    """Manifest-relevant state shared between run()'s body and its exit handler.

    A single mutable object so the outer ``except`` can always write a coherent
    interrupted/crashed manifest no matter how far the run got -- avoiding both
    the unbound-local trap (an exception before a bare local is assigned) and a
    250-line re-indent to wrap the live block in a try.
    """

    sink: _JobSink | None = None
    all_jobs: list[dict[str, Any]] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    # Sources that finished but returned INCOMPLETE results (raised
    # PartialSourceError). Distinct from failed_sources -- their rows are kept --
    # but surfaced separately so a partial/all-failed scrape is never read as a
    # clean run. Carried on state so the interrupt-path manifest stays honest.
    degraded_sources: list[str] = field(default_factory=list)
    phase_reached: str = "scraping"
    fn_stats: dict[str, int] = field(default_factory=_empty_fit_stats)
    # The overlap (wave-1) background enrichment thread, exposed so run()'s exit
    # handler can join its in-flight enrichment before the final flush/manifest.
    wave1_thread: threading.Thread | None = None
    # The run's cooperative stop signal, exposed so the interrupt handler can set
    # it before joining wave 1 -- the coordinator's completion loop honors it to
    # cancel pending LLM calls and drain promptly (the bounded join then becomes a
    # real drain rather than a wait for the full remaining budget).
    stop_event: threading.Event | None = None
    # Holds a wave-1 exception relayed from the background thread, so the interrupt
    # handler can surface it (log, not re-raise -- the interrupt exit code wins)
    # rather than letting it vanish when the thread is joined off the normal path.
    wave1_error: list[BaseException] = field(default_factory=list)
    # Wave-1's per-phase stats and whether the normal path already folded them, so
    # the interrupt handler can account wave-1 enrichment in the manifest exactly
    # once (the normal path folds via _accumulate_enrich_stats; the interrupt path
    # folds here after the join, guarded by the flag against double-counting).
    wave1_result: dict[str, Any] = field(default_factory=dict)
    wave1_accumulated: bool = False
    # Backlog-wave counters, carried on state (like fn_stats) so the interrupt
    # manifest reflects backlog progress the teardown flush actually persisted.
    # remaining is set to the full scoreable count before the wave starts and
    # reconciled after it completes.
    backlog_enriched: int = 0
    backlog_remaining: int = 0
    # Alias of ctx.saturation (shared list), so both manifest paths report
    # truncated queries.
    saturation: list[SaturationRecord] = field(default_factory=list)
    # Rows verified closed by board-diff this run (manifest counter).
    closed_verified: int = 0

    @property
    def written(self) -> int:
        return len(self.sink.rows) if self.sink is not None else 0


def _write_run_manifest(
    output_dir: Path,
    *,
    started_at: datetime,
    all_jobs: list[dict[str, Any]],
    failed_sources: list[str],
    degraded_sources: list[str],
    written: int,
    fn_enriched: int,
    phase_reached: str,
    interrupted: bool,
    backlog_enriched: int = 0,
    backlog_remaining: int = 0,
    saturation: list[SaturationRecord] | None = None,
    closed_verified: int = 0,
    upgraded: int = 0,
) -> None:
    """Write jobs-last-run.json. Written on BOTH the happy and interrupt paths.

    ``phase_reached`` (scraping|detail|enrichment|complete) and ``interrupted``
    let ``jobs status`` surface a recovery line when a run was cut short, so the
    user knows to finish enrichment with ``jobs backfill``.
    """
    run_manifest = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "sources_ok": sorted(
            {
                j.get("source", "")
                for j in all_jobs
                if j.get("source")
                and j.get("source") not in failed_sources
                and j.get("source") not in degraded_sources
            }
        ),
        "sources_failed": failed_sources,
        "sources_degraded": degraded_sources,
        "new_jobs": written,
        "enriched_fit_notes": fn_enriched,
        "backlog_enriched": backlog_enriched,
        "backlog_remaining": backlog_remaining,
        # Snapshot first: an interrupt-path write can race a still-running
        # phase-1 worker's append.
        "saturated_queries": [asdict(rec) for rec in list(saturation or [])],
        "closed_verified": closed_verified,
        "upgraded": upgraded,
        "phase_reached": phase_reached,
        "interrupted": interrupted,
    }
    last_run_path = output_dir / "jobs-last-run.json"
    # Atomic write (temp + fsync + os.replace, mirroring csv_io.atomic_write_rows):
    # `jobs run --json` reads this file back and pipes it downstream, so a partial
    # write must never leave malformed JSON that a consumer then parses. os.replace
    # is atomic on POSIX, so a reader sees either the old file or the complete new
    # one. A mid-write failure unlinks the temp file before propagating to the
    # best-effort handler below.
    tmp_path = last_run_path.with_suffix(last_run_path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(run_manifest, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, last_run_path)
        log.debug("Run manifest written to %s", last_run_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        log.warning("Could not write run manifest: %s", exc)


def run(
    plugin: JobSearchPlugin,
    output_dir: Path,
    ephemeral_dir: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
    dry_run: bool = False,
    no_enrich: bool = False,
    sources_override: list[str] | None = None,
    suppress_live: bool = False,
) -> int:
    """Run all enabled scrapers and append new rows to ``output_dir/jobs.csv``.

    Returns the process-style exit code (0 success, 1 on failed sources /
    persistence degraded / I/O error). Performs no argparse or ``sys.exit()`` --
    the CLI layer is responsible for exit handling.

    ``suppress_live`` forces plain mode (no pinned live block) regardless of TTY;
    the ``jobs run --json`` path sets it so stdout stays a clean JSON channel
    while diagnostics still go to stderr.

    Thin wrapper around :func:`_run_impl` that owns the manifest-on-every-exit
    guarantee: a ``_RunState`` carries the manifest-relevant state, and ANY exit
    that escapes ``_run_impl`` -- a Ctrl-C/SIGTERM during scraping or the
    scrape/enrich overlap window (the longest state, the launchd-SIGTERM case),
    or any crash -- writes an ``interrupted=True`` manifest at the phase reached
    before re-raising. Clean completion writes ``interrupted=False`` inside
    ``_run_impl``. dry-run writes no manifest (it returns before the write).
    """
    started_at = datetime.now(timezone.utc)
    state = _RunState()
    try:
        return _run_impl(
            plugin,
            output_dir,
            ephemeral_dir,
            ai=ai,
            context_text=context_text,
            dry_run=dry_run,
            no_enrich=no_enrich,
            sources_override=sources_override,
            suppress_live=suppress_live,
            started_at=started_at,
            state=state,
        )
    except BaseException:
        # Manifest on every exit. Best-effort guarded flush so a disk error here
        # never masks the original exception (e.g. degrading a SIGINT to exit 1).
        # dry-run has no sink, so there is nothing to persist and no manifest to
        # write (a dry-run leaves the previous run's manifest untouched).
        if not dry_run:
            # Cooperatively stop, then JOIN the overlap (wave-1) background thread
            # before the final flush so its in-flight enrichment lands on disk
            # instead of being abandoned. Setting stop_event makes the wave-1
            # enrichment loop cancel its pending LLM calls and drain promptly, so
            # the BOUNDED join is a real drain in the common case -- not a wait for
            # the full remaining budget. The bound still caps the wait so a second
            # Ctrl-C is never swallowed; if wave 1 does not settle within it, the
            # daemon is left to expire on its own enrich_timeout and we disclose
            # the under-count below. Set unconditionally -- the scrape-phase
            # handler may already have set it, and a redundant set is harmless.
            if state.stop_event is not None:
                state.stop_event.set()
            wave1_thread = state.wave1_thread
            if wave1_thread is not None and wave1_thread.is_alive():
                wave1_thread.join(timeout=_WAVE1_INTERRUPT_JOIN_SECONDS)
                if wave1_thread.is_alive():
                    # The daemon outlived the bound; its remaining results are not
                    # in state.wave1_result, so the manifest will under-report and
                    # the thread may still be flushing rows to jobs.csv. Disclose
                    # rather than fail silently.
                    log.warning(
                        "wave-1 enrichment did not settle within %ss; manifest "
                        "enrichment counts under-report rows still being written "
                        "to %s",
                        _WAVE1_INTERRUPT_JOIN_SECONDS,
                        state.sink.csv_path if state.sink is not None else "jobs.csv",
                    )
            # Surface a relayed wave-1 exception so it does not vanish on this
            # path. Do NOT re-raise -- the interrupt's exit code wins; the error
            # only needs to be visible for diagnosis.
            if state.wave1_error:
                log.error(
                    "wave-1 enrichment raised before the interrupt: %s",
                    state.wave1_error[0],
                )
            # Fold wave-1's enrichment counts into the manifest unless the normal
            # path already did (guards against double-counting).
            if not state.wave1_accumulated and state.wave1_result:
                _accumulate_enrich_stats(state.fn_stats, state.wave1_result)
                state.wave1_accumulated = True
            sink = state.sink
            if sink is not None:
                try:
                    sink.flush()
                except OSError as exc:
                    log.warning(
                        "PERSISTENCE failure on interrupt: could not save %s (%s)",
                        sink.csv_path,
                        exc,
                    )
            _write_run_manifest(
                output_dir,
                started_at=started_at,
                all_jobs=state.all_jobs,
                failed_sources=state.failed_sources,
                degraded_sources=state.degraded_sources,
                written=state.written,
                fn_enriched=state.fn_stats["enriched"],
                backlog_enriched=state.backlog_enriched,
                backlog_remaining=state.backlog_remaining,
                saturation=state.saturation,
                closed_verified=state.closed_verified,
                upgraded=state.sink.upgraded if state.sink is not None else 0,
                phase_reached=state.phase_reached,
                interrupted=True,
            )
        raise


def _run_impl(
    plugin: JobSearchPlugin,
    output_dir: Path,
    ephemeral_dir: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
    dry_run: bool = False,
    no_enrich: bool = False,
    sources_override: list[str] | None = None,
    suppress_live: bool = False,
    started_at: datetime,
    state: _RunState,
) -> int:
    """The run pipeline. Mutates ``state`` so the :func:`run` wrapper can write a
    coherent manifest on any abrupt exit. See :func:`run`."""
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        load_existing_jobs,
        read_rows,
    )

    csv_path = output_dir / "jobs.csv"
    clear_stale_adjacent_lock(csv_path)
    lock_path = jobs_lock_path(ephemeral_dir)
    ai_cfg = ai or AIConfig()

    if not plugin.scraper.enabled:
        Console.warning(
            "Scraper disabled. Set plugins.job_search.scraper.enabled: true "
            "in .dd-config.yaml"
        )
        return 0

    from daily_driver.plugins.job_search.jobs_archive import load_archive_dedup

    # Announce-then-wait on contention, with a bounded give-up. Returning a clean
    # non-zero here (rather than raising) avoids the run() wrapper writing a
    # misleading interrupted=True manifest for a run that never started.
    lock_stack = ExitStack()
    try:
        lock_stack.enter_context(
            file_lock(
                lock_path,
                timeout=LOCK_WAIT_TIMEOUT_SECONDS,
                on_contention=workspace_busy_notice,
            )
        )
    except TimeoutError:
        Console.error(LOCK_GIVEUP_MESSAGE)
        return 1
    try:
        known_urls, known_keys, header = load_existing_jobs(csv_path)
        # Captured under the same lock as the dedup seed: the sink's flush
        # rewrites the WHOLE file, so it must carry these rows through
        # field-for-field or every pre-existing row would be wiped by the
        # first flush.
        _, preexisting_rows = read_rows(csv_path)
        # A hand-edited row with MORE cells than the header parks the overflow
        # under DictReader's None key, which the rewrite's DictWriter drops
        # (extrasaction="ignore"). The old append-only path never rewrote
        # those bytes; now that every enriching run does, surface the trim
        # instead of letting it happen silently.
        ragged = [r for r in preexisting_rows if None in r]
        if ragged:
            log.warning(
                "jobs.csv has %d row(s) with more cells than the header; the "
                "extra trailing cells will be dropped on the next save: %s",
                len(ragged),
                ", ".join(str(r.get("Link") or "(no link)") for r in ragged),
            )

        # Reconcile the description sidecar against the live jobs.csv URLs
        # BEFORE the archive-dedup merge, so the GC key is the live set only
        # (archived rows never need descriptions -- the store is a pure cache).
        # Real runs only: dry-run writes nothing.
        if not dry_run:
            from daily_driver.plugins.job_search.scraper.descriptions import (
                gc_descriptions,
            )

            dropped = gc_descriptions(csv_path, known_urls)
            if dropped and not suppress_live:
                Console.info(f"Cleaned up {dropped} orphaned description(s).")

        # Union archive-table dedup state so triaged listings (pruned to
        # jobs.archive.csv) are never re-discovered. Verification-closed rows
        # are deliberately absent from the union (re-discoverable); their URLs
        # arrive in reopen_watch so a re-discovery is announced loudly.
        archive_urls, archive_keys, reopen_watch = load_archive_dedup(csv_path)
        known_urls |= archive_urls
        known_keys |= archive_keys

        if not header:
            header = CANONICAL_HEADER
            # dry-run writes nothing at all (in-memory single pass), so skip
            # initializing the file too -- the append path never fires under it.
            if not dry_run:
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(csv_path, "w", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow(header)
                except OSError as exc:
                    log.error("Cannot initialize %s: %s", csv_path, exc)
                    # Overwrite any stale "complete" manifest from a prior run so
                    # `jobs status` reflects this failed run, not the last good one.
                    _write_run_manifest(
                        output_dir,
                        started_at=started_at,
                        all_jobs=[],
                        failed_sources=[],
                        degraded_sources=[],
                        written=0,
                        fn_enriched=0,
                        phase_reached="scraping",
                        interrupted=True,
                    )
                    return 1
    finally:
        lock_stack.close()

    log.info(
        "Loaded %d existing URLs, %d existing keys from %s",
        len(known_urls),
        len(known_keys),
        csv_path,
    )

    from daily_driver.plugins.job_search.scraper.discovery import (
        SWEEP_PLATFORMS,
        load_matched_boards,
    )

    # Boards the discovery sweep matched: sorted for a stable scrape order,
    # keyed by platform for the board adapters to union with pins.
    discovered_boards = {
        platform: tuple(sorted(load_matched_boards(ephemeral_dir, platform)))
        for platform in SWEEP_PLATFORMS
    }
    for platform, boards in discovered_boards.items():
        if boards:
            log.info(
                "[%s] %d discovered boards from the matched cache",
                platform,
                len(boards),
            )

    # Carry the merged dedup set on the context so adapters that build their
    # URL deterministically (Apple, Wellfound) can short-circuit during
    # pagination instead of waiting for the post-scrape filter below.
    ctx = ScrapeContext(
        plugin=plugin,
        ai=ai_cfg,
        context_text=context_text,
        known_urls=frozenset(known_urls),
        discovered_boards=discovered_boards,
    )
    # Expose the cooperative stop signal so run()'s interrupt handler can set it
    # before joining the wave-1 thread (turning the bounded join into a drain).
    state.stop_event = ctx.stop_event
    # Alias the shared saturation list so the interrupt-path manifest reports
    # queries flagged before the interrupt.
    state.saturation = ctx.saturation

    # Coarse run phase for the manifest (scraping -> detail -> enrichment ->
    # complete), held on ``state`` so the run() wrapper's exit handler reads the
    # phase actually reached. Closures (incl. the wave-1 thread) update it.
    def _set_phase(p: str) -> None:
        state.phase_reached = p

    title = "Job search run (dry-run)" if dry_run else "Job search run"
    # The live block renders on any TTY; verbosity controls only how much scrolls
    # above it. enlighten pins the bars in a terminal scroll region and the
    # terminal scrolls log lines above them, so WARN+ (and INFO/DEBUG heartbeats
    # under -v/-vv) stream live as the run progresses while the block stays
    # pinned. Non-TTY (cron/pipe) -- and an unresponsive TTY -- get plain line
    # mode, no block. Quiet mode ("errors only") suppresses the block entirely;
    # the plain-mode progress lines are dropped by RunProgress under quiet too.
    # --json (suppress_live) also forces plain mode so stdout stays clean JSON.
    tty = Console.live_progress_enabled(suppress=suppress_live)
    with (
        live_log_window(tty),
        RunProgress(Console.get_log_console(), tty=tty, title=title) as rp,
    ):
        scrape_group = rp.group("Scraping sources")
        # Rows keyed by source id: created pending when the run order resolves,
        # flipped to running on start, frozen on completion. Tolerant of a
        # start/done without a prior enabled announcement (e.g. in tests).
        source_rows: dict[str, Item] = {}

        def _row(sid: str) -> Item:
            row = source_rows.get(sid)
            if row is None:
                row = scrape_group.item(_display_name(sid))
                source_rows[sid] = row
            return row

        def _on_enabled(sids: list[str]) -> None:
            # Reserve the whole scrape block in one scroll-region set before any
            # row is pinned: title + group header + one bar per source. Avoids
            # the per-bar region resizes that corrupt the display on iTerm2 /
            # VS Code (gap before the block, duplicate block in scrollback).
            rp.reserve(2 + len(sids))
            for sid in sids:
                _row(sid)

        def _on_started(sid: str) -> None:
            _row(sid).start(note=_slow_source_note(sid))

        def _on_progress(sid: str, completed: int, total: int | None) -> None:
            # Fed from each scraper's ctx.report(done, total) callback; upgrades the row to a live bar.
            _row(sid).progress(completed, total)

        def _on_done(sid: str, ok: bool, detail: str) -> None:
            # A checkpoint-aborted source returns normally from its scraper
            # (rows persisted so far), so the orchestrator reports ok=True --
            # but the checkpoint handler already recorded it failed. The live
            # row must agree with the manifest/exit code, not paint green.
            if ok and sid in state.failed_sources:
                ok = False
                detail = f"{detail} (persistence failed)" if detail else "failed"
            _row(sid).finish(ok, detail)

        # The sink IS the durable-record checkpoint: each source's rows are
        # deduped/filtered/lifted and appended to jobs.csv as it lands (the
        # append-as-completed callback below). dry-run keeps the old in-memory
        # single pass with no sink and no writes.
        sink: _JobSink | None = None
        on_source_result: Callable[[str, list[dict[str, Any]]], object] | None = None
        on_source_checkpoint: Callable[[str, list[dict[str, Any]]], object] | None = (
            None
        )
        if not dry_run:
            from daily_driver.plugins.job_search.scraper.descriptions import (
                load_descriptions,
            )

            sink = _JobSink(
                csv_path=csv_path,
                lock_path=lock_path,
                header=header,
                known_urls=known_urls,
                known_keys=known_keys,
                plugin=plugin,
                preexisting_rows=preexisting_rows,
                descriptions=load_descriptions(csv_path),
            )
            sink.reopen_watch = reopen_watch
            state.sink = sink

            def _on_source_result(source_id: str, jobs: list[dict[str, Any]]) -> None:
                # Per-source isolation: an append OSError (raised as ScraperError
                # by the sink) must mark just this source failed and let the
                # remaining sources finish -- not kill the whole run with later
                # sources lost. The sink already rolled back its state on failure
                # (no phantom dedup/rows), so the source is cleanly absent.
                try:
                    sink.append_source(source_id, jobs)
                except ScraperError as exc:
                    log.error(
                        "[%s] could not persist scraped rows, marking source "
                        "failed: %s",
                        source_id,
                        exc,
                    )
                    if source_id not in state.failed_sources:
                        state.failed_sources.append(source_id)

            on_source_result = _on_source_result

            def _on_source_checkpoint(
                source_id: str, jobs: list[dict[str, Any]]
            ) -> None:
                # Per-unit checkpoint path. On a persist failure mark the source
                # failed AND raise CheckpointAborted so the source stops at this
                # unit -- otherwise it would scrape on against a dead disk and a
                # later unit could land rows for a source already reported failed.
                # "failed" then means "stopped at the failure".
                try:
                    sink.append_source(source_id, jobs)
                except ScraperError as exc:
                    log.error(
                        "[%s] could not persist a scraped unit, stopping the "
                        "source and marking it failed: %s",
                        source_id,
                        exc,
                    )
                    if source_id not in state.failed_sources:
                        state.failed_sources.append(source_id)
                    raise CheckpointAborted(str(exc)) from exc

            on_source_checkpoint = _on_source_checkpoint

        # Overlap state (Stage 3). When a phase 2 (Apple) follows phase 1, the
        # phase-1 rows are enriched in a background WAVE while Apple still
        # scrapes; Apple's rows get a second wave afterward. Budgets are SHARED
        # running totals across waves. Enrichment runs on the wave-1 thread here,
        # not the main thread, so the per-enricher SIGINT notifier no-ops (main-
        # thread only); a child thread never receives the KeyboardInterrupt. On an
        # interrupt during the overlap, run()'s handler sets ctx.stop_event and
        # bounded-joins this thread: the coordinator's completion loop honors the
        # event to cancel pending LLM calls and drain what settled, so the join is
        # a cooperative drain in the common case rather than a wait for the full
        # remaining budget. If wave 1 still outlives the bound (a stuck in-flight
        # call), the handler logs a disclosure that the manifest under-reports and
        # the daemon may still be flushing -- it is NOT a hard guarantee that every
        # wave-1 result lands, but it is no longer silently abandoned.
        enrich_cfg = plugin.enrichment
        do_enrich = not (dry_run or no_enrich)

        enrich_group: Group | None = None
        wave1_thread: threading.Thread | None = None
        # Holds wave-1's per-phase stats plus the attempted-identity set
        # (fit_attempted_urls) so wave 2 can exclude those rows and size the
        # shared budget by what wave 1 actually attempted. Aliased to state so
        # the run() interrupt handler can fold wave-1 stats into the manifest and
        # surface a relayed wave-1 exception after the join.
        wave1_result: dict[str, Any] = state.wave1_result
        wave1_error: list[BaseException] = state.wave1_error
        wave1_count = [0]  # phase-1 rows handed to wave 1

        def _run_wave1(phase1_jobs: list[EnrichedJob]) -> None:
            assert sink is not None and enrich_group is not None
            try:
                attempted: dict[str, set[str]] = {}
                d, f = _enrich_wave(
                    plugin,
                    ai_cfg,
                    phase1_jobs,
                    ctx,
                    sink,
                    enrich_group,
                    wave_label="",
                    run_preflight=True,
                    fit_budget=None,  # wave 1 gets the full config budget
                    set_phase=_set_phase,
                    attempted=attempted,
                )
                wave1_result.update(
                    {
                        "detail": d,
                        "fit": f,
                        "fit_attempted_urls": attempted.get("fit_urls", set()),
                    }
                )
            except BaseException as exc:  # noqa: BLE001 -- relayed to main thread
                wave1_error.append(exc)

        def _on_phase1_done(has_phase2: bool) -> None:
            # Only overlap when an Apple phase actually follows -- otherwise the
            # single post-scrape wave on the main thread is simpler and keeps the
            # interrupt notifier live.
            if not (do_enrich and has_phase2) or sink is None:
                return
            nonlocal enrich_group, wave1_thread
            # Hand the wave the REAL sink.rows, not a copy: the enrichers
            # propagate results by replacing list slots (frozen EnrichedJob), and
            # only the live sink.rows is what flush() reads. Wave 1 iterates this
            # LIVE list, so once Apple's concurrent .extend() appends phase-2 rows
            # it may also touch indices >= wave1_count (any rows present when its
            # plan/stitch runs). That is harmless: slot replacement on a Python
            # list is GIL-atomic so it never races Apple's append, and wave 2
            # dedups by the attempted-identity sets (fit URLs / companies), not by
            # index, so a phase-2 row wave 1 happened to enrich is simply excluded
            # from wave 2 rather than re-charged. wave1_count is pinned here only
            # to size wave 2's "is there leftover work" check, not to bound which
            # slots wave 1 writes. The lock is held only to read len consistently.
            with sink._rows_lock:
                phase1_jobs = sink.rows
                wave1_count[0] = len(phase1_jobs)
            enrich_group = rp.group("Enriching jobs")
            wave1_thread = threading.Thread(
                target=_run_wave1, args=(phase1_jobs,), daemon=True
            )
            state.wave1_thread = wave1_thread
            wave1_thread.start()

        def _on_degraded(source_id: str, reason: str) -> None:
            # A source finished but its scrape is incomplete (PartialSourceError);
            # record it as degraded -- distinct from failed -- so the summary and
            # manifest stay honest. Its partial rows are kept (appended normally).
            if source_id not in state.degraded_sources:
                state.degraded_sources.append(source_id)

        all_jobs, scrape_failed, source_results = run_all_scrapers(
            ctx,
            sources_override=sources_override,
            on_sources_enabled=_on_enabled,
            on_source_start=_on_started,
            on_source_progress=_on_progress,
            on_source_done=_on_done,
            on_note=rp.note,
            on_source_result=on_source_result,
            on_source_degraded=_on_degraded,
            # Slow jobspy sources checkpoint each finished (term x country)
            # unit and the multi-board sources each finished board through the
            # failure-isolated append path, so a crash/kill keeps every
            # completed unit (not the whole-source-or-nothing of before). On a
            # persist failure the checkpoint path stops the source (raises
            # CheckpointAborted) rather than scraping on against a dead disk.
            on_source_checkpoint=on_source_checkpoint,
            on_phase1_done=_on_phase1_done,
            # Force Apple headless while the live block owns the terminal.
            force_headless=tty,
        )
        state.all_jobs = all_jobs

        # Board-diff closure: diff stored rows against this run's successful
        # raw board enumerations. Two consecutive misses close a row (Status +
        # Date Closed + Notes annotation, applied in place at flush). A scrape
        # fact like re-sighting, so it runs for --no-enrich too; dry-run has no
        # sink and skips.
        closure_stats: dict[str, Any] = {"closed": 0, "pending_misses": 0}
        if sink is not None and ctx.enumerations:
            from daily_driver.plugins.job_search.scraper.closure import (
                decide_closures,
                load_misses,
                save_misses,
            )

            closures, new_misses, closure_stats = decide_closures(
                preexisting_rows,
                ctx.enumerations,
                load_misses(csv_path),
                today().isoformat(),
            )
            sink.closures = closures
            state.closed_verified = closure_stats["closed"]
            try:
                with sink._disk_lock():
                    save_misses(csv_path, new_misses)
            except OSError as exc:
                # Losing the miss ledger only restarts the 2-miss clock; the
                # run itself is healthy.
                log.warning("Could not persist board-diff miss counts: %s", exc)

        # Merge scraper-level failures with any append-persistence failures the
        # source-result callback recorded; preserve order, no duplicates.
        for sid in scrape_failed:
            if sid not in state.failed_sources:
                state.failed_sources.append(sid)
        failed_sources = state.failed_sources
        degraded_sources = state.degraded_sources

        if failed_sources:
            log.warning("Failed sources: %s", ", ".join(failed_sources))
        if degraded_sources:
            log.warning(
                "Degraded sources (incomplete results, kept what was scraped): %s",
                ", ".join(degraded_sources),
            )

        if sink is not None:
            # Append-time funnel: the sink classified every row as it was written,
            # so the totals and per-source breakdown are already accumulated.
            raw_found = sink.raw_found
            pre_filter = sink.pre_filter
            filtered = sink.loc_filtered
            funnel = sink.funnel
            typed_jobs = sink.rows
        else:
            # dry-run: replicate the former in-memory dedup/filter/lift in one
            # pass so the preview table and Completed line match a real run.
            new_jobs = [
                j
                for j in all_jobs
                if (not j.get("url") or j["url"] not in known_urls)
                and dedup_key(j.get("company", ""), j.get("role", "")) not in known_keys
            ]
            urlless = [j for j in new_jobs if not j.get("url")]
            if urlless:
                log.warning(
                    "Dropping %d jobs with no URL (cannot dedup on future runs)",
                    len(urlless),
                )
            new_jobs = [j for j in new_jobs if j.get("url")]
            log.info("Found %d jobs total, %d new", len(all_jobs), len(new_jobs))
            pre_filter = len(new_jobs)
            new_jobs = [j for j in new_jobs if location_matches(j, plugin)]
            filtered = pre_filter - len(new_jobs)
            if filtered:
                log.info("Filtered %d jobs by location preferences", filtered)
            raw_found = sum(
                len(jobs)
                for _, jobs in source_results
                if not isinstance(jobs, Exception)
            )
            funnel = _per_source_funnel(source_results, known_urls, known_keys, plugin)
            typed_jobs = [_enriched_from_scraped(j) for j in new_jobs]

        scrape_group.done(f"{raw_found} found, {pre_filter} new")

        # Colour each finished source bar by what it found. The funnel persists
        # past the `with` block (it is not a scope), so it is reused for the
        # summary after teardown.
        for sid, counts in funnel.items():
            row = source_rows.get(sid)
            if row is not None:
                row.show_breakdown(_source_breakdown_segments(counts))

        # Accumulate enrichment counters straight into state so the run()
        # wrapper's manifest reflects partial progress on an interrupt.
        fn_stats = state.fn_stats
        # Backlog (never-enriched pre-existing rows) counters. Disjoint from
        # fn_stats/written by design: those must stay new-rows-only.
        folded_candidates: list[EnrichedJob] = []
        folded_stats = _empty_fit_stats()
        folded_leftover_budget = 0
        backlog_scoreable_total = 0
        backlog_no_desc = 0
        if not do_enrich:
            # dry-run avoids writes entirely; --no-enrich appends the lifted rows
            # unenriched for a later backfill. Render no enrichment bars and
            # report zero counters.
            log.info(
                "[%s] skipping enrichment (no detail/LLM calls)",
                "dry-run" if dry_run else "no-enrich",
            )
        else:
            # The sink is the durable checkpoint: each wave mutates its rows in
            # place and flushes jobs.csv per completed phase and every ~25 LLM
            # results. An interrupt/crash is caught by the run() wrapper, which
            # flushes the partial pass and writes the interrupted manifest; a
            # mid-enrichment loss is at most one flush window (jobs backfill resumes).
            assert sink is not None  # not dry_run -> sink exists
            # Wave 1 (phase-1 rows) may already be running in a background thread
            # (started at on_phase1_done). Join it, then enrich the remaining
            # (Apple) rows as wave 2 with the budget wave 1 left.
            # Cumulative fit attempts across the new-row waves: sizes the
            # backlog wave's leftover budget, so missing a wave here would
            # over-grant it.
            fit_attempted_all: set[str] = set()
            if wave1_thread is not None:
                wave1_thread.join()
                if wave1_error:
                    raise wave1_error[0]
                assert enrich_group is not None
                _accumulate_enrich_stats(fn_stats, wave1_result)
                state.wave1_accumulated = True
                fit_attempted_all |= wave1_result.get("fit_attempted_urls", set())
                # Wave 2 receives the WHOLE row list (so its slot mutations and
                # the periodic flush both target sink.rows directly). Rows ALREADY
                # ATTEMPTED in wave 1 -- enriched or failed -- are excluded so a
                # failed wave-1 row is not retried (and double-charged) in wave 2;
                # backfill is the retry path. The fit budget is a shared running
                # total: wave 2 gets what wave 1 left, counted by jobs attempted.
                if len(typed_jobs) > wave1_count[0]:
                    fit_attempted = frozenset(
                        wave1_result.get("fit_attempted_urls", set())
                    )
                    wave2_attempted: dict[str, set[str]] = {}
                    _, f2 = _enrich_wave(
                        plugin,
                        ai_cfg,
                        typed_jobs,
                        ctx,
                        sink,
                        enrich_group,
                        wave_label=" (wave 2)",
                        run_preflight=False,  # wave 1 already probed
                        # Shared running total: wave 2 gets exactly what wave 1
                        # left -- 0 when exhausted (an explicit 0 makes no calls;
                        # the wave still runs its unbudgeted detail enrichment).
                        fit_budget=max(
                            0, enrich_cfg.max_enrich_fit - len(fit_attempted)
                        ),
                        set_phase=_set_phase,
                        exclude_fit_urls=fit_attempted,
                        attempted=wave2_attempted,
                    )
                    _add_stats(fn_stats, f2)
                    fit_attempted_all |= wave2_attempted.get("fit_urls", set())
            else:
                # No overlap (no Apple phase, or it landed nothing): one wave
                # over every row on the main thread, same as the classic path.
                enrich_group = rp.group("Enriching jobs")
                single_attempted: dict[str, set[str]] = {}
                _, f1 = _enrich_wave(
                    plugin,
                    ai_cfg,
                    typed_jobs,
                    ctx,
                    sink,
                    enrich_group,
                    wave_label="",
                    run_preflight=True,
                    fit_budget=None,
                    set_phase=_set_phase,
                    attempted=single_attempted,
                )
                _add_stats(fn_stats, f1)
                fit_attempted_all |= single_attempted.get("fit_urls", set())
            if enrich_group is not None:
                enrich_group.done()

            # Backlog wave: never-enriched pre-existing rows, lifted from the
            # run-start snapshot and hydrated read-only from the sidecar.
            # Enriched with ONLY the fit budget the new-row waves left,
            # cache-only descriptions (backfill parity). Kept off sink.rows so
            # jobs.csv order and new_jobs stay new-rows-only; landed in place
            # at flush via _apply_folded_updates.
            from daily_driver.plugins.job_search.scraper.models import EnrichedJob

            backlog_scoreable: list[EnrichedJob] = []
            for raw_row in sink.preexisting_rows:
                # Raw-cell gate first: the enriched majority of a mature
                # jobs.csv never pays a model lift.
                if (raw_row.get("Date Enriched") or "").strip():
                    continue
                job = EnrichedJob.from_csv_row(raw_row)
                if job.status not in ENRICH_ELIGIBLE_STATUSES:
                    continue
                if job.fit and job.notes:
                    # Scored before the Date Enriched column existed; only
                    # backfill --force-update re-scores. Counting it here
                    # would inflate "remaining" forever.
                    continue
                if not job.description_text:
                    cached = sink._descriptions.get(job.url, "")
                    if cached.strip():
                        job = job.with_updates(description_text=cached)
                if not job.description_text.strip():
                    # Unscorable until a re-scrape heals the description;
                    # entering the wave would re-fetch its detail page every
                    # run for nothing.
                    backlog_no_desc += 1
                    continue
                backlog_scoreable.append(job)
            folded_leftover_budget = max(
                0, enrich_cfg.max_enrich_fit - len(fit_attempted_all)
            )
            backlog_scoreable_total = len(backlog_scoreable)
            # Cap candidates to the leftover budget: the wave's detail phase
            # is otherwise unbudgeted, so this bounds its fetches to rows the
            # fit pass can actually score this run. Zero leftover (or zero
            # scoreable) skips the wave entirely.
            folded_candidates = backlog_scoreable[:folded_leftover_budget]
            state.backlog_remaining = backlog_scoreable_total
            if folded_candidates:
                # The SAME list object the wave mutates via slot replacement --
                # flush_periodic reads sink.folded_rows, so a copy would lose
                # mid-run progress (cf. backfill's sink.rows = jobs).
                sink.folded_rows = folded_candidates
                folded_group = rp.group("Enriching backlog")
                _, folded_stats = _enrich_wave(
                    plugin,
                    ai_cfg,
                    folded_candidates,
                    ctx,
                    sink,
                    folded_group,
                    wave_label=" (backlog)",
                    run_preflight=False,
                    fit_budget=folded_leftover_budget,
                    set_phase=_set_phase,
                    exclude_fit_urls=frozenset(fit_attempted_all),
                    capture_descriptions=False,
                )
                folded_group.done()
                state.backlog_enriched = folded_stats["enriched"]
                state.backlog_remaining = (
                    backlog_scoreable_total - folded_stats["enriched"]
                )

    n = len(typed_jobs)
    log.info(
        "Fit+Notes enriched: %d/%d, %d skipped (budget), %d no description, "
        "%d failed (parse/subprocess)",
        fn_stats["enriched"],
        n,
        fn_stats["skipped_budget"],
        fn_stats.get("no_description", 0),
        fn_stats["failed"],
    )

    # ── Scraping funnel ──────────────────────────────────────────────────────
    # Headline that reconciles every job: found (raw scraped) -> new (not already
    # in csv) -> matched location (the only filter that removes jobs). Intra/
    # cross-run duplicates are the remainder. `funnel` (the per-source breakdown)
    # was computed above while the live bars were up; it is reused here. The
    # re-sighting split is a scrape fact, so it lives here (not under Enrichment)
    # and reports even under --no-enrich.
    still_visible, not_seen, healed = (
        sink.rescan_summary() if sink is not None else (0, 0, 0)
    )
    label_width = max([len(_display_name(sid)) for sid in funnel] + [len("Re-seen")])
    Console.info("Scraping")
    summary = (
        f"  Completed: {raw_found} found -> {pre_filter} new "
        f"-> {len(typed_jobs)} matched location"
    )
    if filtered:
        summary += f" ({filtered} skipped by location)"
    Console.info(summary)
    for sid, c in funnel.items():
        # The grey "other" bar segment (within-run duplicates + url-less rows)
        # has no count of its own in the funnel; surface it here only when
        # present so no datum lives in colour alone.
        other = _funnel_other(c)
        line = (
            f"  {_display_name(sid):<{label_width}}  {c['found']} found, "
            f"{c['new']} new, {c['known']} already in csv, "
            f"{c['loc_skip']} skipped (location)"
        )
        if other > 0:
            line += f", {other} other"
        Console.info(line)
    # Re-sighting freshness split: pre-existing rows the scrape re-confirmed live
    # vs. did not return. "not seen this run" is deliberately not "gone" -- a live
    # job from an unscraped source/window looks identical.
    if still_visible or not_seen:
        reseen_line = (
            f"  {'Re-seen':<{label_width}}  {still_visible} still visible, "
            f"{not_seen} not seen this run"
        )
        if healed:
            reseen_line += f"; {healed} descriptions healed"
        Console.info(reseen_line)
    # Upgraded: aggregator rows rewritten to their board twin's Source/Link.
    # A durable-record rewrite the user should hear about, not discover.
    if sink is not None and sink.upgraded:
        Console.info(
            f"  {'Upgraded':<{label_width}}  {sink.upgraded} row(s) switched to "
            "the company board's record (see log for identities)"
        )
    # Reopened: a job that verification once closed came back in a scrape.
    # Loud -- a genuinely re-posted role or a false-positive closure healing.
    if sink is not None and sink.reopened:
        Console.warning(
            f"  {'Reopened':<{label_width}}  {sink.reopened} previously-closed "
            "job(s) re-discovered (see log for identities)"
        )
    # Verified-closed: absent from a successful full board listing twice in a
    # row. The rows stay in jobs.csv as Status=closed until prune archives
    # them; a false positive resurfaces loudly as Reopened.
    if closure_stats["closed"] or closure_stats["pending_misses"]:
        line = (
            f"  {'Closed':<{label_width}}  {closure_stats['closed']} verified "
            "gone (board-diff)"
        )
        if closure_stats["pending_misses"]:
            line += f", {closure_stats['pending_misses']} pending a second miss"
        Console.warning(line)
    # Degraded sources finished with INCOMPLETE results (a partial-pagination or
    # all-units-failed scrape). Their rows are kept, but call it out so a partial
    # run is never mistaken for a clean one. Distinct from failed sources below.
    if degraded_sources:
        Console.warning(
            "  Degraded sources (incomplete results, kept what was scraped): "
            + ", ".join(_display_name(sid) for sid in degraded_sources)
        )
    # Truncated queries: a query that returned its full cap saw only part of
    # its window. One line per (source, kind) so a mixed LinkedIn run shows
    # BOTH remediations (capped terms are fixable for free; the plateau is
    # not); full per-query detail is in the manifest (saturated_queries) and
    # the -v log. Hints are per-source: each has its own limit knob.
    if ctx.saturation:
        by_group: dict[tuple[str, str], list[SaturationRecord]] = {}
        for rec in ctx.saturation:
            by_group.setdefault((rec.source, rec.kind), []).append(rec)
        for (sid, kind), recs in by_group.items():
            shown = ", ".join(rec.query for rec in recs[:3])
            more = f" (+{len(recs) - 3} more)" if len(recs) > 3 else ""
            hint = _SATURATION_HINTS.get(
                (sid, kind), "results truncated; coverage incomplete"
            )
            Console.warning(
                f"  Saturated  {_display_name(sid)}: {shown}{more} -- {hint}"
            )

    # ── Enrichment funnel ────────────────────────────────────────────────────
    # Only when enrichment actually ran (a dry-run or --no-enrich pass scores
    # nothing, so a zeroed section would be noise).
    no_description_count = fn_stats.get("no_description", 0)
    if do_enrich:
        Console.info("Enrichment")
        fit_line = (
            f"  {'Fit/Notes':<{label_width}}  {fn_stats['enriched']} scored, "
            f"{fn_stats['skipped_budget']} over budget, "
            f"{no_description_count} no description"
        )
        if fn_stats["failed"]:
            fit_line += f", {fn_stats['failed']} failed"
        Console.info(fit_line)
        # Backlog: pre-existing never-enriched rows folded into this run.
        # Remaining rows stay eligible -- the next run (or jobs backfill)
        # picks them up; awaiting-description rows need a re-scrape to heal
        # their description first.
        if backlog_scoreable_total or backlog_no_desc:
            backlog_line = (
                f"  {'Backlog':<{label_width}}  "
                f"{folded_stats['enriched']} scored, "
                f"{state.backlog_remaining} remaining"
            )
            detail_bits = []
            if backlog_no_desc:
                detail_bits.append(f"{backlog_no_desc} awaiting description")
            if folded_stats["failed"]:
                detail_bits.append(f"{folded_stats['failed']} failed")
            if state.backlog_remaining and folded_leftover_budget == 0:
                detail_bits.append("no fit budget left this run")
            if detail_bits:
                backlog_line += " (" + ", ".join(detail_bits) + ")"
            Console.info(backlog_line)
        # Rows with no obtainable description (e.g. a signup-walled posting) are
        # intentionally left un-scored rather than guessed at; call it out so the
        # user knows to re-scrape rather than assume the row was simply skipped.
        if no_description_count:
            Console.warning(
                f"  {no_description_count} job(s) had no description; Fit/Notes "
                "left blank. Delete the row and re-run a scrape to retry."
            )

    if dry_run:
        Console.success(
            f"Dry-run complete: {len(typed_jobs)} new jobs ready (nothing written)."
        )
        _print_dry_run_table(typed_jobs)
        return 1 if failed_sources else 0

    # Rows were already appended per-source during scraping (the durable-record
    # checkpoint). --no-enrich leaves the rows as appended (no rewrite needed).
    assert sink is not None  # dry_run returned above; non-dry-run always has a sink
    written = len(sink.rows)
    skip_note = " (enrichment skipped)" if no_enrich else ""
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    Console.success(
        f"Scraper complete: {written} new jobs appended to {csv_path}.{skip_note} "
        f"Total run time: {_fmt_duration(elapsed)}."
    )

    # Write the completion manifest BEFORE the final flush, so a final-flush
    # failure (raised loudly below) still leaves an honest manifest behind.
    state.phase_reached = "complete"
    _write_run_manifest(
        output_dir,
        started_at=started_at,
        all_jobs=all_jobs,
        failed_sources=failed_sources,
        degraded_sources=state.degraded_sources,
        written=written,
        fn_enriched=fn_stats["enriched"],
        backlog_enriched=state.backlog_enriched,
        backlog_remaining=state.backlog_remaining,
        saturation=ctx.saturation,
        closed_verified=state.closed_verified,
        upgraded=sink.upgraded,
        phase_reached=state.phase_reached,
        interrupted=False,
    )

    if not no_enrich:
        # The final rewrite through the backfill rewrite path persists the
        # in-place enrichment; the lock is taken only for the rewrite, never
        # across LLM calls, so a concurrent prune/backfill is serialized but not
        # starved. If periodic saves degraded mid-run, say so once; then let any
        # final-flush OSError propagate loudly -- a silent disk failure must not
        # look like a healthy run when hours of enrichment live only in memory.
        if sink.persistence_degraded:
            Console.warning(
                f"periodic saves failed during the run "
                f"({sink._degraded_reason}); retrying the final save now"
            )
        sink.flush()
    elif sink.has_resightings() or sink._descriptions_dirty or sink.closures:
        # --no-enrich makes no enrichment rewrite, but scrape facts still need
        # persisting: a re-sighting's refreshed Date Verified / healed
        # description, and any description captured at scrape (the sidecar is
        # only written by flush -- skipping it here would silently drop every
        # scraped description, leaving the backlog wave and backfill nothing
        # to score). This is the one rewrite a --no-enrich run makes.
        try:
            sink.flush()
        except OSError as exc:
            log.warning(
                "PERSISTENCE failure: could not save re-sightings to %s (%s)",
                sink.csv_path,
                exc,
            )

    if failed_sources:
        log.error("Scraper failures: %s", ", ".join(failed_sources))
        return 1

    # A run whose periodic saves degraded -- even when the final flush recovered
    # the data on disk -- exits non-zero so a scripted/scheduled caller treats
    # the run as not-fully-clean and can re-run or alert. The data is persisted;
    # the exit code reports that the run did not proceed normally throughout.
    if not no_enrich and sink.persistence_degraded:
        return 1

    return 0
