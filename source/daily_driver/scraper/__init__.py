"""Job-board scraper package.

Thin public wrapper around the internal ``_impl`` module, which contains
the ported implementation of the original ``scripts/scrape-jobs.py``.
External callers should use ``run()`` / ``run_backfill()`` rather than
importing from ``_impl`` directly.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import _impl
from ._impl import ScraperError

log = logging.getLogger(__name__)


def load_config_file(config_path: Path) -> dict[str, Any]:
    """Load a YAML config file into the raw-dict shape the scraper expects.

    Raises ValueError when the caller points at a legacy ``config.yaml``.
    All scraper settings now
    belong under ``plugins.job_search`` in ``.dd-config.yaml``.
    See docs/configuration.md for the current schema.
    """
    if config_path.name == "config.yaml":
        raise ValueError(
            f"{config_path} is a legacy config file. "
            "All scraper settings have moved to plugins.job_search in .dd-config.yaml. "
            "See docs/configuration.md for the current schema."
        )
    return _impl.load_config(config_path)


def run_backfill(config: dict[str, Any], csv_path: Path) -> None:
    """Re-enrich empty fields in an existing jobs.csv."""
    _impl.backfill(config, csv_path)


def run(
    config: dict[str, Any],
    output_dir: Path,
    *,
    dry_run: bool = False,
) -> int:
    """Run all enabled scrapers and append new rows to ``output_dir/jobs.csv``.

    Returns the process-style exit code (0 success, 1 on failed sources /
    I/O error). Mirrors the behavior of the legacy ``scripts/scrape-jobs.py``
    ``main()`` without performing argparse or ``sys.exit()`` — the CLI layer
    is responsible for exit handling.
    """
    started_at = datetime.now(timezone.utc)
    csv_path = output_dir / "jobs.csv"

    _impl.validate_config(config)

    if not _impl.scraper_cfg(config).enabled:
        print(
            "Scraper disabled. Set plugins.job_search.scraper.enabled: true "
            "in .dd-config.yaml"
        )
        return 0

    known_urls, known_keys, header = _impl.load_existing_jobs(csv_path)

    # Union archive-table dedup state so triaged listings (pruned to
    # jobs.archive.csv) are never re-discovered.
    from daily_driver.core.jobs_archive import load_archive_dedup

    archive_urls, archive_keys = load_archive_dedup(csv_path)
    known_urls |= archive_urls
    known_keys |= archive_keys

    if not header:
        header = _impl.CANONICAL_HEADER
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(header)
        except OSError as exc:
            log.error("Cannot initialize %s: %s", csv_path, exc)
            return 1
    else:
        header = _impl._migrate_legacy_header(csv_path, header)

    log.info(
        "Loaded %d existing URLs, %d existing keys from %s",
        len(known_urls),
        len(known_keys),
        csv_path,
    )

    # Stuff the merged dedup set into the config dict so adapters that build
    # their URL deterministically (Apple, Wellfound) can short-circuit during
    # pagination instead of waiting for the post-scrape filter below.
    config["_known_urls"] = known_urls

    all_jobs, failed_sources = _impl.run_all_scrapers(config)

    new_jobs = [
        j
        for j in all_jobs
        if (not j.get("url") or j["url"] not in known_urls)
        and _impl.dedup_key(j.get("company", ""), j.get("role", "")) not in known_keys
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
    new_jobs = [j for j in new_jobs if _impl.location_matches(j, config)]
    filtered = pre_filter - len(new_jobs)
    if filtered:
        log.info("Filtered %d jobs by location preferences", filtered)

    if failed_sources:
        log.warning("Failed sources: %s", ", ".join(failed_sources))

    if not dry_run:
        _impl.enrich_job_details(new_jobs, config)
    else:
        log.info("[dry-run] skipping enrich_job_details (claude calls)")
    new_jobs = [_impl.normalize_job(j, j.get("source", "")) for j in new_jobs]

    skipped_below_comp = 0
    for job in new_jobs:
        if job.get("status") == "skipped":
            continue
        ok, reason = _impl.comp_meets_threshold(job, config)
        if ok:
            continue
        job["status"] = "skipped"
        existing_notes = (job.get("notes") or "").strip()
        job["notes"] = f"{existing_notes}; {reason}" if existing_notes else reason
        skipped_below_comp += 1
        log.info(
            "[comp-filter] skipped: %s | %s | comp=%r",
            job.get("company", "?"),
            job.get("role", "?"),
            job.get("comp", ""),
        )
    log.info(
        "Comp-threshold filter: %d skipped below $%d USD",
        skipped_below_comp,
        _impl.min_comp_usd(config),
    )

    if dry_run:
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
        product_stats = _impl.enrich_company_descriptions(new_jobs, config)
        fn_stats = _impl.enrich_fit_and_notes(new_jobs, config)

    n = len(new_jobs)
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

    if dry_run:
        for j in new_jobs:
            print(
                f"  [{j['source']:22s}] {j['company']:30s} | "
                f"{j['role']:45s} | {j['location']}"
            )
            print(f"    {j['url']}")
        print(f"\n{len(new_jobs)} new jobs (dry-run, nothing written)")
        return 1 if failed_sources else 0

    written = _impl.append_jobs(csv_path, new_jobs, header)
    print(
        f"Scraper complete: {written} new jobs appended to {csv_path} "
        f"({skipped_below_comp} skipped below comp threshold)"
    )

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
        "skipped_below_comp": skipped_below_comp,
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
        _impl._notify_new_jobs(written, csv_path)
    return 0


__all__ = ["ScraperError", "load_config_file", "run", "run_backfill"]
