"""Board-diff closure: rows absent from their board's full listing get closed.

A full-enumeration board (greenhouse/ashby/workable/workday) returns its
complete current inventory each scrape, so a stored row absent from a
SUCCESSFULLY-fetched listing carries real signal. Closure is deliberately
guarded three ways:

- **Raw listing, not filtered results.** Adapters record every posting URL the
  board returned BEFORE the role filter (``ctx.record_enumeration``); diffing
  filtered results would close a live job whose title merely stopped matching
  the configured roles.
- **Only boards enumerated this run.** A board removed from config, a board
  whose fetch failed, or a board whose pagination came back partial records no
  enumeration, so its rows can never be misread as "gone".
- **Two consecutive misses (mandatory).** Closure archives a row and is the
  most consequential automatic action in the pipeline; a single blip that
  slips past the guards must not trigger it. Miss counts persist in a sidecar
  (``board-diff-misses.json`` beside jobs.csv) and clear the moment a URL
  re-appears.

A closed row gets ``Status: closed`` + ``Date Closed`` + a Notes annotation,
and later leaves for the archive via ``jobs prune`` (closed is a default prune
status). If the closure was a false positive, the archive-dedup split (#148)
lets the job re-surface loudly as ``Reopened``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from daily_driver.core.logging import get_logger
from daily_driver.core.statuses import normalize_status

log = get_logger(__name__)

# Consecutive successful-enumeration misses required before a row closes.
MISS_THRESHOLD = 2

# Statuses closure may touch: the untriaged inbox. Anything the user labeled
# (applied, skipped, ...) is their decision; verification only annotates the
# rows nobody has judged yet.
_CLOSABLE_STATUSES = frozenset({"found", "pending"})


def misses_path(csv_path: Path) -> Path:
    return csv_path.with_name("board-diff-misses.json")


def load_misses(csv_path: Path) -> dict[str, int]:
    """Load the url -> consecutive-miss-count store; {} on absence/corruption.

    A cache of counters, never the source of truth — a malformed file must
    never abort a run, it just restarts the 2-miss clock.
    """
    path = misses_path(csv_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Cannot read %s (%s); miss counts reset", path, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("%s is not an object; miss counts reset", path)
        return {}
    return {
        url: int(count)
        for url, count in raw.items()
        if isinstance(url, str) and isinstance(count, int) and count > 0
    }


def save_misses(csv_path: Path, store: dict[str, int]) -> None:
    """Atomically persist the miss store (temp + fsync + replace)."""
    path = misses_path(csv_path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=0, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def decide_closures(
    rows: list[dict[str, str]],
    enumerations: dict[str, set[str]],
    misses: dict[str, int],
    today_iso: str,
) -> tuple[dict[str, dict[str, str]], dict[str, int], dict[str, Any]]:
    """Diff stored rows against this run's board enumerations.

    ``enumerations`` is keyed by the exact Source-cell display string the
    adapter writes (e.g. "Greenhouse (stripe)"), so row matching needs no
    model lift and only board-diff-capable adapters ever appear.

    Returns (closures, updated_misses, stats): ``closures`` maps a row's URL to
    the cell updates the flush applies in place; ``updated_misses`` is the new
    sidecar content; ``stats`` feeds the run summary/manifest.
    """
    closures: dict[str, dict[str, str]] = {}
    updated = dict(misses)
    first_misses = 0
    for row in rows:
        source_label = (row.get("Source") or "").strip()
        listing = enumerations.get(source_label)
        if listing is None:
            continue  # board not (successfully) enumerated this run
        if normalize_status(row.get("Status") or "") not in _CLOSABLE_STATUSES:
            continue
        url = (row.get("Link") or "").strip()
        if not url:
            continue
        if url in listing:
            if updated.pop(url, None) is not None:
                log.info("Board-diff: %s re-appeared; miss count cleared", url)
            continue
        count = updated.get(url, 0) + 1
        if count < MISS_THRESHOLD:
            updated[url] = count
            first_misses += 1
            log.info(
                "Board-diff: %s absent from %s (miss %d/%d)",
                url,
                source_label,
                count,
                MISS_THRESHOLD,
            )
            continue
        updated.pop(url, None)
        notes = (row.get("Notes") or "").strip()
        suffix = f"[closed: board-diff {today_iso}]"
        closures[url] = {
            "Status": "closed",
            "Date Closed": today_iso,
            "Notes": f"{notes} | {suffix}" if notes else suffix,
        }
        log.warning(
            "Verified closed (board-diff, %d consecutive misses): %s -- %s %s",
            MISS_THRESHOLD,
            row.get("Company", ""),
            row.get("Role", ""),
            url,
        )
    stats = {"closed": len(closures), "pending_misses": first_misses}
    return closures, updated, stats


__all__ = [
    "MISS_THRESHOLD",
    "decide_closures",
    "load_misses",
    "misses_path",
    "save_misses",
]
