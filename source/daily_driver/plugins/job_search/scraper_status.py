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
    path = output_dir / "jobs-last-run.json"
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
    if not csv_path.exists():
        return {}
    counts: dict[str, int] = {}
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # The canonical jobs.csv column is "Status" (capitalized, per
                # EnrichedJob.CSV_COLUMN_TO_ATTR); a lowercase key never matches,
                # so every row would fall through to "unknown".
                state = (row.get("Status") or "").strip().lower() or "unknown"
                counts[state] = counts.get(state, 0) + 1
    except OSError:
        return {}
    return counts


def build_status(output_dir: Path) -> dict[str, Any]:
    """Assemble the full status payload for ``jobs status``.

    Schema version 1. Keys:
      last_run      - dict from jobs-last-run.json, or null
      job_counts    - {state: count} from jobs.csv
      awaiting_action - count of jobs in applied/interviewing states
    """
    csv_path = output_dir / "jobs.csv"
    last_run = load_last_run(output_dir)
    counts = count_jobs_by_state(csv_path)
    awaiting = sum(v for k, v in counts.items() if k in _AWAITING_ACTION_STATES)
    return {
        "last_run": last_run,
        "job_counts": counts,
        "awaiting_action": awaiting,
    }


__all__ = ["build_status", "count_jobs_by_state", "load_last_run"]
