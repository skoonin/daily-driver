"""Scraper orchestration: ScrapeContext, dedup logic, run() / run_backfill()."""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import yaml

from daily_driver.core.config_models import AIConfig
from daily_driver.core.console import Console
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import deferred_logs, get_logger
from daily_driver.core.progress import Item, RunProgress
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

# Display names for the live scraping rows. Source ids are pipeline-internal;
# these are what a user reads. Unmapped ids fall back to a de-underscored form.
_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "weworkremotely": "we work remotely",
    "hn_who_is_hiring": "hn who's hiring",
    "hn_jobs": "hn jobs",
    "jobspy_linkedin": "linkedin",
    "jobspy_indeed": "indeed",
    "jobspy_google": "google",
}


def _display_name(source_id: str) -> str:
    return _SOURCE_DISPLAY_NAMES.get(source_id, source_id.replace("_", " "))


def _fmt_duration(seconds: float) -> str:
    """Human elapsed: ``6m 41s`` for a minute or more, else ``6.1s``."""
    if seconds >= 60:
        minutes, secs = divmod(int(round(seconds)), 60)
        return f"{minutes}m {secs}s"
    return f"{seconds:.1f}s"


def _intra_source_duplicates(jobs: list[dict[str, Any]]) -> int:
    """Count a source's own duplicate rows (same URL or company+role key).

    Apple's locale loop, for example, returns the same posting under several
    locales; this surfaces that on the source row without waiting for the
    cross-source merge.
    """
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    unique = 0
    for job in jobs:
        url = job.get("url", "")
        key = dedup_key(job.get("company", ""), job.get("role", ""))
        if (url and url in seen_urls) or (key and key in seen_keys):
            continue
        if url:
            seen_urls.add(url)
        if key:
            seen_keys.add(key)
        unique += 1
    return len(jobs) - unique


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


def _run_one(
    source_id: str,
    ctx: ScrapeContext,
    on_source_done: Callable[[str, bool, str], None] | None = None,
    on_source_start: Callable[[str], None] | None = None,
) -> list[dict[str, Any]] | Exception:
    """Invoke one scraper and map known failures to exceptions.

    Returns the job list on success, or the caught exception on failure. The
    orchestrator classifies exceptions into failed_sources during merge.

    Brackets the scrape with ``on_source_start(source_id)`` and
    ``on_source_done(source_id, ok, detail)`` so the live display can open a
    per-source row when the work begins (spinner + ticking elapsed) and freeze
    it with the job count or failure reason when it ends. Both callbacks drive
    only the thread-safe progress display, so they are safe to invoke from the
    Phase-1 worker threads. The matching log lines stay on stderr for the debug
    stream.
    """
    scraper_fn = SCRAPERS[source_id]
    timeout = ctx.plugin.scraper.timeout
    log.info("[%s] starting", source_id)
    if on_source_start is not None:
        on_source_start(source_id)
    start = time.perf_counter()
    try:
        jobs = scraper_fn(ctx)
    except HTTPTimeout as exc:
        # HTTPTimeout/HTTPError alias requests exceptions; the stub-less
        # `requests` import types them as Any, so narrow on the way out.
        log.warning("[%s] timed out after %ds", source_id, timeout)
        if on_source_done is not None:
            on_source_done(source_id, False, f"failed (timed out after {timeout}s)")
        return cast(Exception, exc)
    except HTTPError as exc:
        log.warning("[%s] request failed: %s", source_id, exc)
        if on_source_done is not None:
            on_source_done(source_id, False, f"failed ({exc})")
        return cast(Exception, exc)
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] unexpected error: %s", source_id, exc, exc_info=True)
        if on_source_done is not None:
            on_source_done(source_id, False, f"failed ({exc})")
        return exc
    elapsed = time.perf_counter() - start
    log.info("[%s] took %.1fs (%d jobs)", source_id, elapsed, len(jobs))
    # Intra-source duplicates (e.g. apple's locale overlap) are diagnostic, not
    # the dedup the user cares about (already-in-csv); surface only at -v.
    intra_dup = _intra_source_duplicates(jobs)
    if intra_dup:
        log.info(
            "[%s] scrape complete: %d entries were intra-source duplicates",
            source_id,
            intra_dup,
        )
    if on_source_done is not None:
        on_source_done(
            source_id, True, f"{len(jobs)} found in {_fmt_duration(elapsed)}"
        )
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


def _per_source_funnel(
    results: list[tuple[str, list[dict[str, Any]] | Exception]],
    known_urls: set[str],
    known_keys: set[str],
    plugin: JobSearchPlugin,
) -> dict[str, dict[str, int]]:
    """Per-source breakdown: found / new / known (already in csv) / loc_skip.

    Mirrors the global pipeline (cross-source dedup in first-wins order, then
    the already-known check, then the location filter) so the per-source counts
    reconcile with the Completed line. ``dup`` is within-run duplicates
    (intra-source + cross-source), surfaced only for the verbose detail.
    """
    stats: dict[str, dict[str, int]] = {}
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    for source_id, result in results:
        if isinstance(result, Exception):
            continue
        counts = stats.setdefault(
            source_id, {"found": 0, "new": 0, "known": 0, "loc_skip": 0, "dup": 0}
        )
        for job in result:
            counts["found"] += 1
            url = job.get("url", "")
            key = dedup_key(job.get("company", ""), job.get("role", ""))
            if (url and url in seen_urls) or (key and key in seen_keys):
                counts["dup"] += 1
                continue
            if url:
                seen_urls.add(url)
            if key:
                seen_keys.add(key)
            if (url and url in known_urls) or (key and key in known_keys):
                counts["known"] += 1
            elif location_matches(job, plugin):
                counts["new"] += 1
            else:
                counts["loc_skip"] += 1
    return stats


def run_all_scrapers(
    ctx: ScrapeContext,
    *,
    sources_override: list[str] | None = None,
    on_source_done: Callable[[str, bool, str], None] | None = None,
    on_source_start: Callable[[str], None] | None = None,
    on_sources_enabled: Callable[[list[str]], None] | None = None,
) -> tuple[
    list[dict[str, Any]], list[str], list[tuple[str, list[dict[str, Any]] | Exception]]
]:
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
        log.debug("[%s] disabled in config, skipping", sid)

    non_headless = _PLAYWRIGHT_SOURCES
    headless_sources = [sid for sid in enabled if sid not in non_headless]
    visible_sources = [sid for sid in enabled if sid in non_headless]

    # Announce the full run order up front so the display can list every source
    # as pending before any of them start.
    if on_sources_enabled is not None:
        on_sources_enabled(headless_sources + visible_sources)

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
                pool.submit(
                    _run_one, sid, headless_ctx, on_source_done, on_source_start
                ): sid
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
            results.append(
                (sid, _run_one(sid, visible_ctx, on_source_done, on_source_start))
            )

    all_jobs, failed_sources = _merge_and_dedup(results)
    return all_jobs, failed_sources, results


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

    # Carry the merged dedup set on the context so adapters that build their
    # URL deterministically (Apple, Wellfound) can short-circuit during
    # pagination instead of waiting for the post-scrape filter below.
    ctx = ScrapeContext(
        plugin=plugin,
        ai=ai_cfg,
        context_text=context_text,
        known_urls=frozenset(known_urls),
    )

    title = "Job search run (dry-run)" if dry_run else "Job search run"
    # The live block is a normal-mode affordance. At -v / -vv the user wants the
    # log stream itself, live, as the run progresses -- so drop the live block
    # (which would otherwise garble under concurrent worker-thread logs) and let
    # records stream. Line-mode progress lines still print as rows finish.
    verbose = logging.getLogger("daily_driver").getEffectiveLevel() <= logging.INFO
    tty = Console.is_tty() and not verbose
    # Normal mode only: defer logs past the live block so warnings print as a
    # clean section below it instead of cutting into the live region.
    with (
        deferred_logs(tty),
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
            for sid in sids:
                _row(sid)

        def _on_started(sid: str) -> None:
            _row(sid).start()

        def _on_done(sid: str, ok: bool, detail: str) -> None:
            _row(sid).finish(ok, detail)

        all_jobs, failed_sources, source_results = run_all_scrapers(
            ctx,
            sources_override=sources_override,
            on_sources_enabled=_on_enabled,
            on_source_start=_on_started,
            on_source_done=_on_done,
        )

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
        if failed_sources:
            log.warning("Failed sources: %s", ", ".join(failed_sources))
        # pre_filter (not the post-location count) so "new" matches the
        # Completed line and isn't conflated with location matches.
        scrape_group.done(f"{len(all_jobs)} found, {pre_filter} new")

        # Cross from the dict-based source boundary into the typed pipeline:
        # every surviving merged dict is validated through RawScrapedJob and
        # lifted to a frozen EnrichedJob. The rest operates on these.
        typed_jobs: list[EnrichedJob] = [_enriched_from_scraped(j) for j in new_jobs]

        if dry_run:
            log.info("[dry-run] skipping enrichment (claude calls)")
            product_stats = {"enriched": 0, "skipped_cached": 0, "failed": 0}
            fn_stats = {
                "enriched": 0,
                "skipped_budget": 0,
                "skipped_no_desc": 0,
                "failed": 0,
            }
        else:
            # Create all three phases up front so they read as pending while the
            # earlier phases run.
            enrich_group = rp.group("Enriching jobs")
            detail_phase = enrich_group.phase("Detail pages")
            product_phase = enrich_group.phase("Company products")
            fit_phase = enrich_group.phase("Fit and notes")

            detail_phase.start()
            typed_jobs, detail_stats = enrich_job_details_typed(
                typed_jobs, ctx, progress=detail_phase.advance
            )
            detail_phase.done(
                f"{detail_stats['enriched']} enriched, "
                f"{detail_stats['skipped']} skipped ({detail_stats['total']} total)"
            )

            product_phase.start()
            typed_jobs, product_stats = enrich_company_descriptions_typed(
                typed_jobs, ctx, progress=product_phase.advance
            )
            product_phase.done(
                f"{product_stats['enriched']} enriched, "
                f"{product_stats['skipped_cached']} cached, "
                f"{product_stats['failed']} failed"
            )

            fit_phase.start()
            typed_jobs, fn_stats = enrich_fit_and_notes_typed(
                typed_jobs, ctx, progress=fit_phase.advance
            )
            fit_phase.done(
                f"{fn_stats['enriched']} enriched, "
                f"{fn_stats['skipped_budget']} skipped (budget), "
                f"{fn_stats['failed']} failed"
            )
            enrich_group.done()

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
    # Per-source breakdown + a headline that reconciles every job: found (raw
    # scraped) -> new (not already in csv) -> matched location (the only filter
    # that removes jobs). Intra/cross-run duplicates are the remainder.
    funnel = _per_source_funnel(source_results, known_urls, known_keys, plugin)
    raw_found = sum(c["found"] for c in funnel.values())
    summary = (
        f"Completed: {raw_found} found -> {pre_filter} new "
        f"-> {len(typed_jobs)} matched location"
    )
    if filtered:
        summary += f" ({filtered} skipped by location)"
    Console.info(summary)
    if funnel:
        width = max(len(_display_name(sid)) for sid in funnel)
        for sid, c in funnel.items():
            Console.info(
                f"  {_display_name(sid):<{width}}  {c['found']} found, "
                f"{c['new']} new, {c['known']} already in csv, "
                f"{c['loc_skip']} skipped (location)"
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
