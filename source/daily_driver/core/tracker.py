from __future__ import annotations

import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, List

import yaml
from pydantic import BaseModel, ConfigDict, Field

from daily_driver.core.clock import now, today
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.core.workspace import Workspace

_logger = get_logger("tracker")

# Convention, not enforcement — `tracker add/update` accepts any string but
# emits a one-line stderr nudge when the resolved status is outside the set
# applicable to its category (and not already in use by another entry).
# Silenced by `tracker.warn_unknown_status: false` in .dd-config.yaml.
RECOMMENDED_STATUSES: tuple[str, ...] = (
    "open",
    "in-progress",
    "blocked",
    "done",
    "ruled-out",
)

# job-category lifecycle mirrors scraper.models.JobStatus values plus the
# tracker-only `dropped` terminal state (was `archived` pre-rename).
JOB_RECOMMENDED_STATUSES: tuple[str, ...] = (
    "found",
    "skipped",
    "applied",
    "rejected",
    "dropped",
)


# Terminal lifecycle states (entry won't progress): union across categories.
# status.py uses this to exclude entries from "stalled"; the job-prune default
# (jobs_archive.DEFAULT_PRUNE_STATUSES) is the job-terminal subset.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"done", "ruled-out", "dropped", "rejected", "closed"}
)


def _recommended_for_category(category: str) -> tuple[str, ...]:
    if category == "job":
        return JOB_RECOMMENDED_STATUSES
    return RECOMMENDED_STATUSES


class TrackerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    tags: list[str] = Field(default_factory=list)
    link: str | None = None
    notes: str = ""
    next_action: str | None = None
    due: date | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class TrackerFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[TrackerEntry] = Field(default_factory=list)


def _serialize_entry(entry: TrackerEntry) -> dict[str, Any]:
    raw = entry.model_dump()
    # Convert datetime/date to ISO strings so yaml.safe_dump round-trips cleanly.
    raw["created_at"] = entry.created_at.isoformat()
    raw["updated_at"] = entry.updated_at.isoformat()
    if entry.due is not None:
        raw["due"] = entry.due.isoformat()
    return raw


class Tracker:
    """Facade: owns load / save / CRUD against {output_dir}/tracker.yaml.

    The tracker is bound to a Workspace and resolves its storage path as
    {workspace.root}/{config.daily_driver.output_dir}/tracker.yaml.
    All timestamps use daily_driver.core.clock so FROZEN_TIME applies in tests.
    Writes are protected by an exclusive flock on ephemeral_dir/tracker.lock.
    """

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._path = workspace.output_dir / "tracker.yaml"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> TrackerFile:
        if not self._path.exists():
            return TrackerFile(entries=[])
        text = self._path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not data:
            return TrackerFile(entries=[])
        return TrackerFile.model_validate(data)

    def _lock_path(self) -> Path:
        return self._workspace.ephemeral_dir / "tracker.lock"

    def _save_unlocked(self, data: TrackerFile) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "entries": [_serialize_entry(e) for e in data.entries]
        }
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent, prefix=".tracker-", suffix=".yaml"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(payload, fh, sort_keys=False, default_flow_style=False)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def save(self, data: TrackerFile) -> None:
        with file_lock(self._lock_path(), shared=False):
            self._save_unlocked(data)

    def _warn_unknown_status(
        self,
        status: str,
        existing_entries: list[TrackerEntry],
        category: str,
    ) -> None:
        """Print a one-line stderr nudge when status is outside the recommended set.

        The recommended set is category-aware — `job` entries warn against
        anything outside the JobStatus lifecycle; other categories use the
        generic tuple. Suppressed when (a) the workspace config sets
        warn_unknown_status=False, (b) the status is in the category's
        recommended set, or (c) another entry in the same workspace already
        uses the same custom status.
        """
        if not self._workspace.config.tracker.warn_unknown_status:
            return
        recommended_set = _recommended_for_category(category)
        if status in recommended_set:
            return
        if any(e.status == status for e in existing_entries):
            return
        recommended = ", ".join(recommended_set)
        _logger.warning(
            "'%s' is not in the recommended set (%s)\n"
            "         Set tracker.warn_unknown_status: false in "
            ".dd-config.yaml to silence",
            status,
            recommended,
        )

    def add(
        self,
        *,
        category: str,
        title: str,
        status: str | None = None,
        tags: list[str] | None = None,
        link: str | None = None,
        notes: str | None = None,
        next_action: str | None = None,
        due: date | None = None,
        extras: dict[str, Any] | None = None,
    ) -> TrackerEntry:
        tracker_cfg = self._workspace.config.tracker
        cat_cfg = tracker_cfg.categories.get(category)

        direct_fields: dict[str, Any] = {
            "category": category,
            "title": title,
            "status": status,
            "tags": tags,
            "link": link,
            "notes": notes,
            "next_action": next_action,
            "due": due,
        }
        extras_dict: dict[str, Any] = extras or {}

        if cat_cfg is not None:
            missing = [
                f
                for f in cat_cfg.required
                if not direct_fields.get(f) and not extras_dict.get(f)
            ]
            if missing:
                raise ValueError(
                    f"category '{category}' requires fields: {', '.join(missing)}"
                )

        if status is None:
            status = "open"

        ts = now()

        with file_lock(self._lock_path(), shared=False):
            data = self.load()

            self._warn_unknown_status(status, data.entries, category)

            max_n = 0
            for entry in data.entries:
                if entry.category == category:
                    try:
                        n = int(entry.id.split("-")[-1])
                        if n > max_n:
                            max_n = n
                    except ValueError:
                        pass
            entry_id = f"{category}-{max_n + 1:03d}"

            new_entry = TrackerEntry(
                id=entry_id,
                category=category,
                title=title,
                status=status,
                created_at=ts,
                updated_at=ts,
                tags=tags or [],
                link=link,
                notes=notes or "",
                next_action=next_action,
                due=due,
                extras=extras_dict,
            )
            data.entries.append(new_entry)
            self._save_unlocked(data)
            return new_entry

    def update(
        self, entry_id: str, *, append_note: str | None = None, **changes: Any
    ) -> TrackerEntry:
        with file_lock(self._lock_path(), shared=False):
            data = self.load()
            for i, entry in enumerate(data.entries):
                if entry.id == entry_id:
                    current = entry.model_dump()
                    # Merge extras over the freshly-loaded entry inside the lock
                    # so supplied keys overwrite same-name keys while unmentioned
                    # keys persist — a plain update() would replace the whole dict.
                    supplied_extras = changes.pop("extras", None)
                    current.update(changes)
                    if supplied_extras is not None:
                        current["extras"] = {
                            **(current.get("extras") or {}),
                            **supplied_extras,
                        }
                    # Append note from the freshly-loaded entry inside the lock —
                    # building the joined string in the caller would lose a
                    # concurrent append (the stale read-modify-write window).
                    if append_note is not None:
                        existing_notes = current.get("notes") or ""
                        current["notes"] = (
                            existing_notes + "\n" + append_note
                            if existing_notes
                            else append_note
                        )
                    current["updated_at"] = now()
                    updated = TrackerEntry.model_validate(current)
                    # Exclude the entry being updated from the "already in use"
                    # check — otherwise its own prior status would always mask
                    # the warning when the user changes it.
                    if "status" in changes:
                        peers = [e for e in data.entries if e.id != entry_id]
                        self._warn_unknown_status(
                            updated.status, peers, updated.category
                        )
                    data.entries[i] = updated
                    self._save_unlocked(data)
                    return updated
            raise KeyError(f"entry '{entry_id}' not found")

    def delete(self, entry_id: str) -> TrackerEntry:
        """Remove a single entry by id. Raises KeyError if not found."""
        with file_lock(self._lock_path(), shared=False):
            data = self.load()
            for i, entry in enumerate(data.entries):
                if entry.id == entry_id:
                    del data.entries[i]
                    self._save_unlocked(data)
                    return entry
            raise KeyError(f"entry '{entry_id}' not found")

    def prune(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        older_than: date | None = None,
        dry_run: bool = False,
    ) -> list[TrackerEntry]:
        """Remove entries matching all provided filters. Returns deleted entries.

        At least one filter must be provided — pruning everything is too easy
        to typo, so callers must opt in. With dry_run=True returns the
        candidate set without persisting changes.
        """
        if category is None and status is None and older_than is None:
            raise ValueError(
                "prune requires at least one filter (--category, --status, --older-than)"
            )

        with file_lock(self._lock_path(), shared=False):
            data = self.load()

            def matches(entry: TrackerEntry) -> bool:
                if category is not None and entry.category != category:
                    return False
                if status is not None and entry.status != status:
                    return False
                if older_than is not None and entry.updated_at.date() >= older_than:
                    return False
                return True

            removed = [e for e in data.entries if matches(e)]
            if dry_run or not removed:
                return removed

            data.entries = [e for e in data.entries if not matches(e)]
            self._save_unlocked(data)
            return removed

    def get(self, entry_id: str) -> TrackerEntry:
        """Return a single entry by id. Raises KeyError if not found."""
        for entry in self.load().entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(f"entry '{entry_id}' not found")

    def list(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        since: date | None = None,
    ) -> list[TrackerEntry]:
        entries = self.load().entries
        if category is not None:
            entries = [e for e in entries if e.category == category]
        if status is not None:
            entries = [e for e in entries if e.status == status]
        if tag is not None:
            entries = [e for e in entries if tag in e.tags]
        if since is not None:
            entries = [e for e in entries if e.updated_at.date() >= since]
        return entries

    # NOTE: `List[TrackerEntry]` (not `list[TrackerEntry]`) — the Tracker class
    # defines a `list` method above, and under `from __future__ import annotations`
    # mypy resolves a bare `list` in this return annotation to that method rather
    # than the builtin. typing.List dodges the shadow cleanly.
    def follow_ups(self, *, overdue: bool = False) -> List[TrackerEntry]:
        entries = [e for e in self.load().entries if e.next_action is not None]
        if overdue:
            cutoff = today()
            entries = [e for e in entries if e.due is not None and e.due < cutoff]
        return entries

    def stats(self) -> dict[str, Any]:
        entries = self.load().entries
        by_category: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for entry in entries:
            by_category[entry.category] = by_category.get(entry.category, 0) + 1
            by_status[entry.status] = by_status.get(entry.status, 0) + 1
        return {
            "total": len(entries),
            "by_category": by_category,
            "by_status": by_status,
        }
