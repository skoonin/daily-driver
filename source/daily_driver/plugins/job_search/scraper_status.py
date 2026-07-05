"""scraper_status: read jobs-last-run.json and jobs.csv to surface run metadata."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

# States where the user is passively waiting on an external response.
_AWAITING_ACTION_STATES = {"applied", "interviewing"}


def load_last_run(output_dir: Path) -> dict[str, Any] | None:
    """Return parsed jobs-last-run.json or None when the file is absent/corrupt."""
    return _load_json(output_dir / "jobs-last-run.json")


def load_last_verify(output_dir: Path) -> dict[str, Any] | None:
    """Return parsed jobs-last-verify.json or None when absent/corrupt."""
    return _load_json(output_dir / "jobs-last-verify.json")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
        return None
    except (OSError, json.JSONDecodeError):
        return None


def count_jobs_by_state(csv_path: Path) -> dict[str, int]:
    """Return a mapping of status -> count for every row in jobs.csv.

    Missing or unreadable csv returns an empty dict rather than raising.
    """
    from daily_driver.core.statuses import normalize_status

    if not csv_path.exists():
        return {}
    counts: dict[str, int] = {}
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # The canonical jobs.csv column is "Status" (capitalized, per
                # EnrichedJob.CSV_COLUMN_TO_ATTR); a lowercase key never matches,
                # so every row would fall through to "unknown". normalize_status
                # (not bare .lower()) maps underscores/spaces to hyphens so a
                # hand-edited `Ruled_Out` groups with the canonical `ruled-out`.
                state = normalize_status(row.get("Status") or "") or "unknown"
                counts[state] = counts.get(state, 0) + 1
    except OSError:
        return {}
    return counts


def count_unscored_backlog(csv_path: Path) -> int:
    """Count active rows still awaiting their first fit scoring.

    Mirrors the runner's backlog-wave gate (empty ``Date Enriched``, status
    still enrichment-eligible, not scored before the column existed) minus the
    description check: a row with no cached description is still unscored
    backlog — it just cannot be scored until a re-scrape heals it.

    Missing or unreadable csv returns 0 rather than raising.
    """
    from daily_driver.core.statuses import normalize_status
    from daily_driver.plugins.job_search.scraper.models import (
        ENRICH_ELIGIBLE_STATUSES,
        parse_fit,
    )

    if not csv_path.exists():
        return 0
    backlog = 0
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if (row.get("Date Enriched") or "").strip():
                    continue
                status = normalize_status(row.get("Status") or "")
                if status not in ENRICH_ELIGIBLE_STATUSES:
                    continue
                # Scored before the Date Enriched column existed; counting it
                # would inflate the backlog forever. parse_fit (not raw-cell
                # truthiness) mirrors the runner's gate: a hand-edited
                # unparseable Fit cell is still unscored there.
                if (
                    parse_fit(row.get("Fit")) is not None
                    and (row.get("Notes") or "").strip()
                ):
                    continue
                backlog += 1
    except OSError:
        return 0
    return backlog


def build_status(output_dir: Path, state_dir: Path | None = None) -> dict[str, Any]:
    """Assemble the full status payload for ``jobs status``.

    Schema version 1. Keys:
      last_run      - dict from jobs-last-run.json, or null
      last_verify   - dict from jobs-last-verify.json, or null
      job_counts    - {state: count} from jobs.csv
      awaiting_action - count of jobs in applied/interviewing states
      unscored_backlog - count of active rows with no fit scoring yet
      discovery     - per-platform board-discovery sweep state (boards matched,
                      slugs swept, last sweep timestamp); {} when no sweep has
                      run or ``state_dir`` is not given
    """
    from daily_driver.plugins.job_search.scraper.discovery import sweep_ages

    csv_path = output_dir / "jobs.csv"
    last_run = load_last_run(output_dir)
    counts = count_jobs_by_state(csv_path)
    awaiting = sum(v for k, v in counts.items() if k in _AWAITING_ACTION_STATES)
    return {
        "last_run": last_run,
        "last_verify": load_last_verify(output_dir),
        "job_counts": counts,
        "awaiting_action": awaiting,
        "unscored_backlog": count_unscored_backlog(csv_path),
        "discovery": sweep_ages(state_dir) if state_dir is not None else {},
    }


__all__ = [
    "build_status",
    "count_jobs_by_state",
    "count_unscored_backlog",
    "load_last_run",
    "load_last_verify",
]
