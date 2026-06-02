"""Scraper orchestration: ScrapeContext, dedup logic, run() / run_backfill()."""

from __future__ import annotations

import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import yaml

from daily_driver.core.config_models import AIConfig
from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.integrations.notify import desktop_notify
from daily_driver.plugins.job_search.config import JobSearchPlugin, SourceToggle
from daily_driver.plugins.job_search.jobs_lock import jobs_lock_path
from daily_driver.plugins.job_search.scraper.sources import SCRAPERS
from daily_driver.plugins.job_search.scraper.sources._http import (
    HTTPError,
    HTTPTimeout,
    country_names,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.models import (
        EnrichedJob,
        NormalizedJob,
    )

log = get_logger(__name__)


@dataclass(frozen=True)
class ScrapeContext:
    """Typed inputs threaded through the scraper layer.

    Replaces the raw ``{"job_search": {...}, "ai": {...}, "context": ...,
    "_known_urls": ...}`` dict that every accessor used to re-validate.
    """

    plugin: JobSearchPlugin
    ai: AIConfig = field(default_factory=AIConfig)
    context_text: str = ""  # was the transient config["context"]
    known_urls: frozenset[str] = field(
        default_factory=frozenset
    )  # was config["_known_urls"]


class ScraperError(RuntimeError):
    """Raised on unrecoverable scraper errors.

    Library code raises this instead of calling sys.exit() so the CLI layer
    can decide how to report and exit.
    """


# ── Config ────────────────────────────────────────────────────────────────────


def load_config(config_path: Path) -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as f:
        loaded: dict[str, Any] = yaml.safe_load(f) or {}
        return loaded


# ── Source-toggle helper ─────────────────────────────────────────────────────


_T = TypeVar("_T", bound=SourceToggle)


def source_toggle(plugin: JobSearchPlugin, key: str, toggle_type: type[_T]) -> _T:
    """Return the typed toggle for a source, or a default instance if absent/wrong type.

    Sources whose per-source knobs live on a SourceToggle subclass read them
    through this helper: an unconfigured (or bare-bool) source yields a fresh
    ``toggle_type()`` carrying that subclass's defaults.
    """
    toggle = plugin.sources.get(key)
    return toggle if isinstance(toggle, toggle_type) else toggle_type()


def home_city(plugin: JobSearchPlugin) -> str:
    """Return the configured home city from the locations block."""
    loc = plugin.locations
    return (loc.home_city if loc and loc.home_city else None) or "Vancouver, BC"


def countries_list(plugin: JobSearchPlugin) -> list[str]:
    """Return the configured ISO country codes, defaulting to US + CA."""
    loc = plugin.locations
    return list(loc.countries) if loc and loc.countries else ["US", "CA"]


def location_matches(job: dict[str, Any], plugin: JobSearchPlugin) -> bool:
    """Check whether a job's location matches the configured allow-list.

    Accepts if any of:
      - remote: true and job location contains "remote" (or is empty/missing)
      - job location contains any entry from locations.cities
      - job location contains a country name from locations.countries
    Returns True (accept) when no locations block is configured.
    """
    loc_cfg = plugin.locations
    if loc_cfg is None:
        return True

    loc = job.get("location", "").lower()
    if not loc:
        # Empty location implies remote-friendly; accept when remote is enabled
        return loc_cfg.remote

    if loc_cfg.remote and "remote" in loc:
        return True

    for city in loc_cfg.cities:
        if city.lower() in loc:
            return True

    for code in loc_cfg.countries:
        for name in country_names(code):
            if name.lower() in loc:
                return True

    return False


# ── Dedup helpers ─────────────────────────────────────────────────────────────


_WHITESPACE_RE = re.compile(r"\s+")


def _dedup_norm(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", s.lower().strip())


def dedup_key(company: str, role: str) -> str:
    """Normalized dedup key for cross-site duplicate detection.

    Lowercases and collapses whitespace in both fields so the same job posted
    on RemoteOK and LinkedIn produces an identical key.

    For typed callers, prefer ``dedup_key_for(job: NormalizedJob)``.
    """
    return f"{_dedup_norm(company)}::{_dedup_norm(role)}"


def dedup_key_for(job: NormalizedJob) -> str:  # noqa: F821
    """Typed dedup key: operates directly on NormalizedJob.

    Equivalent to ``dedup_key(job.company, job.role)`` but skips the dict-key
    plumbing in the orchestrator. Both forms must produce identical keys —
    test_dedup_typed_matches_legacy guards that invariant.
    """
    return f"{_dedup_norm(job.company)}::{_dedup_norm(job.role)}"


# Location strings scrapers emit for fully-remote roles. All collapse to "Remote"
# so downstream consumers see one canonical value.
_REMOTE_LOCATION_ALIASES: frozenset[str] = frozenset(
    {
        "",
        "anywhere",
        "worldwide",
        "remote - anywhere",
        "remote, worldwide",
    }
)

# Role-title suffixes appended by some scrapers to signal remote eligibility.
# Stripped here so dedup and display see clean titles.
_REMOTE_ROLE_SUFFIXES: tuple[str, ...] = (" (remote)", "(remote)", " - remote")


def _enriched_from_scraped(job: dict[str, Any]) -> EnrichedJob:  # noqa: F821
    """Lift one merged source dict into a fully-typed ``EnrichedJob``.

    The source-adapter boundary stays dict-based: scrapers emit raw dicts
    (``comp`` display string, ISO ``date_found``, optional ``description_text``).
    This validates that dict through ``RawScrapedJob`` — the one place the
    pipeline crosses from untyped wire data into the typed models — then runs
    the standard ``from_raw`` -> ``from_normalized`` transitions. ``description_text``
    is threaded separately because ``RawScrapedJob`` does not model enrichment
    fields.
    """
    import datetime as dt

    from daily_driver.plugins.job_search.scraper.models import (
        EnrichedJob,
        NormalizedJob,
        RawScrapedJob,
    )

    date_found = job.get("date_found")
    if isinstance(date_found, str) and date_found:
        try:
            date_found_val: dt.date = dt.date.fromisoformat(date_found)
        except ValueError:
            date_found_val = dt.date.today()  # noqa: DTZ011
    elif isinstance(date_found, dt.date):
        date_found_val = date_found
    else:
        date_found_val = dt.date.today()  # noqa: DTZ011

    raw = RawScrapedJob.model_validate(
        {
            "company": job.get("company", ""),
            "role": job.get("role", "") or "(unknown)",
            "url": job.get("url", ""),
            "source": job.get("source", "") or "unknown",
            "location": job.get("location", ""),
            "comp_display": job.get("comp", "") or "",
            "date_found": date_found_val,
        }
    )
    enriched = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    desc = job.get("description_text", "")
    if desc:
        enriched = enriched.model_copy(update={"description_text": desc})
    return enriched


# Sources that require a full (non-headless) browser. SourceToggle has
# extra="forbid" so a config-based `type: playwright` key is rejected by
# pydantic -- browser classification must live in code, not config.
_PLAYWRIGHT_SOURCES: frozenset[str] = frozenset({"apple"})


def _ctx_with_headless(ctx: ScrapeContext, headless: bool) -> ScrapeContext:
    """Return a new ScrapeContext with job_search.scraper.headless overridden.

    Scrapers read headless via ``ctx.plugin.scraper.headless``; handing each
    phase its own context lets the orchestrator force headless mode per phase
    without threading a kwarg through every scraper function.
    """
    return replace(
        ctx,
        plugin=ctx.plugin.model_copy(
            update={
                "scraper": ctx.plugin.scraper.model_copy(update={"headless": headless})
            }
        ),
    )


def _run_one(source_id: str, ctx: ScrapeContext) -> list[dict[str, Any]] | Exception:
    """Invoke one scraper and map known failures to exceptions.

    Returns the job list on success, or the caught exception on failure. The
    orchestrator classifies exceptions into failed_sources during merge.

    Emits an unconditional user-facing start/finish pair on stdout (bypassing
    quiet-mode and verbosity gates) so `daily-driver jobs run` shows progress
    before any scraper completes. The matching log lines stay on stderr for
    the debug stream.
    """
    scraper_fn = SCRAPERS[source_id]
    timeout = ctx.plugin.scraper.timeout
    user_console = Console.get_user_console()
    user_console.print(f"Now checking {source_id}...")
    log.info("[%s] starting", source_id)
    start = time.perf_counter()
    try:
        jobs = scraper_fn(ctx)
    except HTTPTimeout as exc:
        # HTTPTimeout/HTTPError alias requests exceptions; the stub-less
        # `requests` import types them as Any, so narrow on the way out.
        log.warning("[%s] timed out after %ds", source_id, timeout)
        user_console.print(f"  {source_id}: failed (timed out after {timeout}s)")
        return cast(Exception, exc)
    except HTTPError as exc:
        log.warning("[%s] request failed: %s", source_id, exc)
        user_console.print(f"  {source_id}: failed ({exc})")
        return cast(Exception, exc)
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] unexpected error: %s", source_id, exc, exc_info=True)
        user_console.print(f"  {source_id}: failed ({exc})")
        return exc
    elapsed = time.perf_counter() - start
    log.info("[%s] took %.1fs (%d jobs)", source_id, elapsed, len(jobs))
    user_console.print(f"  {source_id}: {len(jobs)} jobs ({elapsed:.1f}s)")
    return jobs


def _merge_and_dedup(
    results: list[tuple[str, list[dict[str, Any]] | Exception]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge per-source results, deduplicating by URL and company+role key.

    First-scraper-wins: iteration order of `results` determines which job wins
    a dedup collision. Exceptions are collected into failed_sources.
    """
    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    failed_sources: list[str] = []

    for source_id, result in results:
        if isinstance(result, Exception):
            failed_sources.append(source_id)
            continue
        for job in result:
            url = job.get("url", "")
            key = dedup_key(job.get("company", ""), job.get("role", ""))
            if (url and url in seen_urls) or (key and key in seen_keys):
                continue
            if url:
                seen_urls.add(url)
            if key:
                seen_keys.add(key)
            all_jobs.append(job)

    return all_jobs, failed_sources


def run_all_scrapers(
    ctx: ScrapeContext,
    *,
    sources_override: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run all enabled scrapers and deduplicate results within this run.

    Two phases:
      Phase 1 — headless-safe sources run in parallel via ThreadPoolExecutor
      Phase 2 — non-headless sources (apple) run serially

    Phase 2 stays serial by design: running multiple visible Firefox windows
    concurrently is RAM-heavy and makes bot detection easier. Deduplicates by
    both URL and company+role key so the same job appearing on multiple boards
    is only kept once (first scraper wins).

    When ``sources_override`` is provided, only those source IDs run regardless
    of the ``sources`` toggles in config. Caller is responsible for
    validating IDs against ``SCRAPERS``.
    """
    cfg = ctx.plugin.scraper
    source_cfg = ctx.plugin.sources
    workers = cfg.parallel_workers

    if sources_override is not None:
        override = set(sources_override)
        enabled = [sid for sid in SCRAPERS if sid in override]
        disabled = [sid for sid in SCRAPERS if sid not in override]
        log.info("[--sources] running only: %s", ", ".join(enabled) or "(none)")
    else:

        def _is_enabled(sid: str) -> bool:
            if sid.startswith("jobspy_"):
                site = sid[len("jobspy_") :]
                toggle = source_cfg.get("jobspy")
                if toggle is None or not toggle.enabled:
                    return False
                return getattr(toggle, site, True)
            toggle = source_cfg.get(sid)
            return toggle.enabled if toggle is not None else False

        enabled = [sid for sid in SCRAPERS if _is_enabled(sid)]
        disabled = [sid for sid in SCRAPERS if not _is_enabled(sid)]
    for sid in disabled:
        log.info("[%s] disabled in config, skipping", sid)

    non_headless = _PLAYWRIGHT_SOURCES
    headless_sources = [sid for sid in enabled if sid not in non_headless]
    visible_sources = [sid for sid in enabled if sid in non_headless]

    results: list[tuple[str, list[dict[str, Any]] | Exception]] = []

    # Phase 1: headless, parallel
    if headless_sources:
        headless_ctx = _ctx_with_headless(ctx, True)
        log.info(
            "[phase1] running %d headless scrapers (%s), %d workers",
            len(headless_sources),
            ", ".join(headless_sources),
            workers,
        )
        pool = ThreadPoolExecutor(max_workers=max(1, workers))
        try:
            futures = {
                pool.submit(_run_one, sid, headless_ctx): sid
                for sid in headless_sources
            }
            for fut in as_completed(futures):
                sid = futures[fut]
                results.append((sid, fut.result()))
        except KeyboardInterrupt:
            # Drop pending (unstarted) futures and stop waiting on the pool.
            # In-flight HTTP requests cannot be killed mid-call; they run to
            # their per-source ``timeout`` before their threads exit. The CLI
            # boundary catches this re-raise and prints a clean message.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

    # Phase 2: non-headless, serial (preserves pre-parallel behavior)
    if visible_sources:
        visible_ctx = _ctx_with_headless(ctx, False)
        log.info(
            "[phase2] running %d non-headless scrapers serially (%s)",
            len(visible_sources),
            ", ".join(visible_sources),
        )
        for sid in visible_sources:
            results.append((sid, _run_one(sid, visible_ctx)))

    return _merge_and_dedup(results)


# ── Notification ─────────────────────────────────────────────────────────────


def _notify_new_jobs(count: int, csv_path: Path) -> None:
    desktop_notify(
        "Job Scraper",
        f"{count} new jobs found",
        open_url=csv_path.as_uri(),
        subtitle=csv_path.name,
    )


# ── Public entry points ──────────────────────────────────────────────────────


def load_config_file(config_path: Path) -> dict[str, Any]:
    """Load a YAML config file into the raw-dict shape the scraper expects.

    Scraper settings live under ``plugins.job_search`` in ``.dd-config.yaml``.
    See docs/configuration.md for the current schema.
    """
    return load_config(config_path)


def run_backfill(
    plugin: JobSearchPlugin,
    csv_path: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
) -> None:
    """Re-enrich empty fields in an existing jobs.csv."""
    from daily_driver.plugins.job_search.scraper.csv_io import backfill

    ctx = ScrapeContext(plugin=plugin, ai=ai or AIConfig(), context_text=context_text)
    backfill(ctx, csv_path)


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


def run(
    plugin: JobSearchPlugin,
    output_dir: Path,
    *,
    ai: AIConfig | None = None,
    context_text: str = "",
    dry_run: bool = False,
    sources_override: list[str] | None = None,
) -> int:
    """Run all enabled scrapers and append new rows to ``output_dir/jobs.csv``.

    Returns the process-style exit code (0 success, 1 on failed sources /
    I/O error). Performs no argparse or ``sys.exit()`` — the CLI layer is
    responsible for exit handling.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        append_jobs_typed,
        load_existing_jobs,
    )
    from daily_driver.plugins.job_search.scraper.enrichment import (
        enrich_company_descriptions_typed,
        enrich_fit_and_notes_typed,
        enrich_job_details_typed,
    )

    started_at = datetime.now(timezone.utc)
    csv_path = output_dir / "jobs.csv"
    lock_path = jobs_lock_path(csv_path)
    ai_cfg = ai or AIConfig()

    Console.info("Starting jobs run...")

    if not plugin.scraper.enabled:
        Console.warning(
            "Scraper disabled. Set plugins.job_search.scraper.enabled: true "
            "in .dd-config.yaml"
        )
        return 0

    from daily_driver.plugins.job_search.jobs_archive import load_archive_dedup

    with file_lock(lock_path):
        known_urls, known_keys, header = load_existing_jobs(csv_path)

        # Union archive-table dedup state so triaged listings (pruned to
        # jobs.archive.csv) are never re-discovered.
        archive_urls, archive_keys = load_archive_dedup(csv_path)
        known_urls |= archive_urls
        known_keys |= archive_keys

        if not header:
            header = CANONICAL_HEADER
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(header)
            except OSError as exc:
                log.error("Cannot initialize %s: %s", csv_path, exc)
                return 1

    log.info(
        "Loaded %d existing URLs, %d existing keys from %s",
        len(known_urls),
        len(known_keys),
        csv_path,
    )
    Console.info(
        f"Loaded existing job index ({len(known_urls)} URLs, {len(known_keys)} keys)."
    )

    # Carry the merged dedup set on the context so adapters that build their
    # URL deterministically (Apple, Wellfound) can short-circuit during
    # pagination instead of waiting for the post-scrape filter below.
    ctx = ScrapeContext(
        plugin=plugin,
        ai=ai_cfg,
        context_text=context_text,
        known_urls=frozenset(known_urls),
    )

    if sources_override:
        Console.info(f"Scraping selected sources: {', '.join(sources_override)}")
    else:
        Console.info("Scraping enabled sources from config...")
    all_jobs, failed_sources = run_all_scrapers(ctx, sources_override=sources_override)

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

    Console.info(f"Scrape complete: {len(all_jobs)} total jobs, {len(new_jobs)} new.")
    log.info("Found %d jobs total, %d new", len(all_jobs), len(new_jobs))

    pre_filter = len(new_jobs)
    new_jobs = [j for j in new_jobs if location_matches(j, plugin)]
    filtered = pre_filter - len(new_jobs)
    if filtered:
        Console.info(f"Filtered {filtered} jobs by location preferences.")
        log.info("Filtered %d jobs by location preferences", filtered)

    if failed_sources:
        log.warning("Failed sources: %s", ", ".join(failed_sources))

    # Cross from the dict-based source boundary into the typed pipeline:
    # every surviving merged dict is validated through RawScrapedJob and lifted
    # to a frozen EnrichedJob. The rest of the pipeline operates on these.
    typed_jobs: list[EnrichedJob] = [_enriched_from_scraped(j) for j in new_jobs]

    if not dry_run:
        Console.info("Enriching job details...")
        typed_jobs = enrich_job_details_typed(typed_jobs, ctx)
    else:
        Console.info("Dry-run mode: skipping job-detail enrichment.")
        log.info("[dry-run] skipping enrich_job_details (claude calls)")

    if dry_run:
        Console.info("Dry-run mode: skipping product + fit/notes enrichment.")
        log.info(
            "[dry-run] skipping enrich_company_descriptions and enrich_fit_and_notes (claude calls)"
        )
        product_stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
        fn_stats = {
            "enriched": 0,
            "skipped_budget": 0,
            "skipped_no_desc": 0,
            "failed": 0,
        }
    else:
        Console.info(
            f"Enriching {len(typed_jobs)} jobs via claude "
            "(company product + fit/notes; this may take a few minutes)..."
        )
        typed_jobs, product_stats = enrich_company_descriptions_typed(typed_jobs, ctx)
        typed_jobs, fn_stats = enrich_fit_and_notes_typed(typed_jobs, ctx)

    n = len(typed_jobs)
    log.info(
        "Fit+Notes enriched: %d/%d, %d skipped (budget), %d failed (parse/subprocess)",
        fn_stats["enriched"],
        n,
        fn_stats["skipped_budget"],
        fn_stats["failed"],
    )
    log.info(
        "Product enriched: %d/%d, %d skipped (cached), %d failed",
        product_stats["enriched"],
        n,
        product_stats["skipped_cached"],
        product_stats["failed"],
    )
    Console.info(
        "Enrichment complete: "
        f"fit+notes {fn_stats['enriched']}/{n} "
        f"({fn_stats['failed']} failed, {fn_stats['skipped_budget']} skipped for budget), "
        f"product {product_stats['enriched']}/{n} ({product_stats['failed']} failed)."
    )

    # Reconciling funnel so the user can account for every job. Location is the
    # only filter that REMOVES jobs.
    Console.info(
        f"Funnel: {len(all_jobs)} scraped → {pre_filter} new "
        f"→ {pre_filter - filtered} after location "
        f"→ {len(typed_jobs)} {'ready' if dry_run else 'to write'}."
    )

    if dry_run:
        Console.success(
            f"Dry-run complete: {len(typed_jobs)} new jobs ready (nothing written)."
        )
        _print_dry_run_table(typed_jobs)
        return 1 if failed_sources else 0

    # Re-acquire the sentinel only for the append. The lock was dropped during
    # enrichment above so slow LLM calls don't block concurrent prune/backfill.
    with file_lock(lock_path):
        written = append_jobs_typed(csv_path, typed_jobs, header)
    Console.success(f"Scraper complete: {written} new jobs appended to {csv_path}.")

    run_manifest = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "sources_ok": sorted(
            {
                j.get("source", "")
                for j in all_jobs
                if j.get("source") and j.get("source") not in failed_sources
            }
        ),
        "sources_failed": failed_sources,
        "new_jobs": written,
        "enriched_fit_notes": fn_stats["enriched"],
        "enriched_product": product_stats["enriched"],
    }
    last_run_path = output_dir / "jobs-last-run.json"
    try:
        last_run_path.write_text(
            json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8"
        )
        log.info("Run manifest written to %s", last_run_path)
    except OSError as exc:
        log.warning("Could not write run manifest: %s", exc)

    if failed_sources:
        log.error("Scraper failures: %s", ", ".join(failed_sources))
        return 1

    if written > 0:
        _notify_new_jobs(written, csv_path)
    return 0
