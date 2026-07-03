"""Sidecar persistence for scraped/fetched job description text.

``EnrichedJob.description_text`` is not a jobs.csv column (the 15-column
canonical header is frozen), so it would otherwise be re-fetched on every
backfill. This module persists it separately, keyed by URL, as
``descriptions.jsonl`` beside ``jobs.csv`` -- one ``{"url": ..., "text": ...}``
object per line, source-agnostic (LinkedIn, Indeed, HN, or a future source all
share the one store).

Callers hold the jobs ``file_lock`` for the whole read-enrich-write lifecycle
already (see ``runner._JobSink``), so this module does no locking of its own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from daily_driver.core.logging import get_logger

log = get_logger(__name__)


def descriptions_path(csv_path: Path) -> Path:
    """The sidecar path for a given jobs.csv path: same directory, fixed name."""
    return csv_path.with_name("descriptions.jsonl")


def load_descriptions(csv_path: Path) -> dict[str, str]:
    """Load the url->text store, or ``{}`` on any absence/corruption.

    This is a cache, never the source of truth -- a missing file, a malformed
    line, or a whole-file read failure must never abort a run. A malformed
    line is skipped (with a warning) rather than treated as fatal.
    """
    path = descriptions_path(csv_path)
    store: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return store
    except OSError as exc:
        log.warning(
            "Cannot read %s, starting with an empty description store: %s", path, exc
        )
        return store
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            url = record["url"]
            store[url] = record["text"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning("Skipping malformed line %d in %s: %s", line_number, path, exc)
            continue
    return store


def atomic_write_descriptions(csv_path: Path, store: dict[str, str]) -> None:
    """Rewrite the sidecar atomically: temp file + flush/fsync + os.replace.

    Mirrors ``csv_io.atomic_write_rows``'s durability discipline. On a
    mid-write failure the partial temp file is unlinked before the error
    propagates, so a crash never leaves a stray ``.tmp`` beside the store.
    """
    path = descriptions_path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for url, text in store.items():
                f.write(json.dumps({"url": url, "text": text}) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def gc_descriptions(
    csv_path: Path, live_urls: set[str], store: dict[str, str] | None = None
) -> int:
    """Drop sidecar entries whose URL is no longer in ``jobs.csv``.

    The store is a pure cache keyed by live jobs.csv URLs; archived rows do not
    need descriptions, so an entry orphaned by a manual deletion or a prune has
    no future consumer and only grows the file (LinkedIn-dominated). Reconcile
    against ``live_urls`` and rewrite atomically, returning the dropped count.
    No write when nothing is dropped.

    Pass ``store`` when the caller already holds a freshly-loaded copy (the
    backfill path) to avoid a redundant read of the same file under the same
    lock; otherwise it is loaded here.

    Callers hold the jobs ``file_lock`` for the read-reconcile-write window
    already, so this does no locking of its own.
    """
    if store is None:
        store = load_descriptions(csv_path)
    kept = {url: text for url, text in store.items() if url in live_urls}
    dropped = len(store) - len(kept)
    if dropped:
        atomic_write_descriptions(csv_path, kept)
    return dropped


__all__ = [
    "descriptions_path",
    "load_descriptions",
    "atomic_write_descriptions",
    "gc_descriptions",
]
