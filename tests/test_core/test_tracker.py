"""Tests for daily_driver.core.tracker — Tracker CRUD, IDs, locking, YAML round-trip."""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import daily_driver.core.clock as clock_mod
from daily_driver.core.tracker import Tracker, TrackerFile
from daily_driver.core.workspace import Workspace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace.init(tmp_path)


@pytest.fixture
def workspace_with_job_required(tmp_path: Path) -> Workspace:
    """Workspace whose TrackerConfig requires 'company' extra for category 'job'."""
    config_text = """\
daily_driver:
  output_dir: .
tracker:
  categories:
    task: {required: [title]}
    job: {required: [title, company]}
"""
    marker = tmp_path / ".dd-config.yaml"
    marker.write_text(config_text, encoding="utf-8")
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir(exist_ok=True)
    return Workspace.discover_or_fail(override=tmp_path)


# ---------------------------------------------------------------------------
# 1. Monotonic IDs per category
# ---------------------------------------------------------------------------


def test_add_assigns_monotonic_id_per_category(workspace: Workspace) -> None:
    tracker = Tracker(workspace)

    t1 = tracker.add(category="task", title="Alpha")
    t2 = tracker.add(category="task", title="Beta")
    t3 = tracker.add(category="task", title="Gamma")

    j1 = tracker.add(category="job", title="Job Alpha")
    j2 = tracker.add(category="job", title="Job Beta")

    assert t1.id == "task-001"
    assert t2.id == "task-002"
    assert t3.id == "task-003"
    assert j1.id == "job-001"
    assert j2.id == "job-002"


# ---------------------------------------------------------------------------
# 2. Frozen-time timestamps
# ---------------------------------------------------------------------------


def test_add_uses_frozen_time(workspace: Workspace) -> None:
    frozen = datetime(2026, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen
    try:
        tracker = Tracker(workspace)
        entry = tracker.add(category="task", title="Frozen task")
        assert entry.created_at == frozen
        assert entry.updated_at == frozen
    finally:
        clock_mod.FROZEN_TIME = None


# ---------------------------------------------------------------------------
# 3. Required-fields validation from TrackerConfig
# ---------------------------------------------------------------------------


def test_add_validates_required_fields_from_config(
    workspace_with_job_required: Workspace,
) -> None:
    tracker = Tracker(workspace_with_job_required)

    # Missing 'company' in extras → ValueError (contract: category config requires "company")
    with pytest.raises((ValueError, Exception)):
        tracker.add(category="job", title="Some Role")

    # Contract: providing 'company' via extras should satisfy required-field validation.
    # NOTE: Stream A validates required fields via `locals().get(f)` which only matches
    # direct function parameters — not keys within the `extras` dict. As a result, passing
    # extras={"company": "Acme"} still raises ValueError. This test documents the
    # expected contract; if it fails, Stream A's required-field check needs to be updated
    # to inspect extras when the field is not a direct parameter.
    entry = tracker.add(category="job", title="Some Role", extras={"company": "Acme"})
    assert entry.category == "job"
    assert entry.extras["company"] == "Acme"


# ---------------------------------------------------------------------------
# 4. update() bumps updated_at, preserves created_at
# ---------------------------------------------------------------------------


def test_update_bumps_updated_at(workspace: Workspace) -> None:
    t1 = datetime(2026, 1, 10, 8, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 11, 12, 0, 0, tzinfo=timezone.utc)

    clock_mod.FROZEN_TIME = t1
    try:
        tracker = Tracker(workspace)
        entry = tracker.add(category="task", title="Time test")
        assert entry.created_at == t1
        assert entry.updated_at == t1

        clock_mod.FROZEN_TIME = t2
        updated = tracker.update(entry.id, notes="updated note")
        assert updated.created_at == t1
        assert updated.updated_at == t2
    finally:
        clock_mod.FROZEN_TIME = None


# ---------------------------------------------------------------------------
# 5. update() with unknown ID raises KeyError
# ---------------------------------------------------------------------------


def test_update_missing_id_raises_keyerror(workspace: Workspace) -> None:
    tracker = Tracker(workspace)
    with pytest.raises(KeyError):
        tracker.update("task-999", status="done")


# ---------------------------------------------------------------------------
# 6. list() filters by category, status, tag
# ---------------------------------------------------------------------------


def test_list_filters_by_category_status_tag(workspace: Workspace) -> None:
    tracker = Tracker(workspace)

    tracker.add(category="task", title="Write tests", tags=["dev"], status="open")
    tracker.add(
        category="task", title="Review PR", tags=["dev", "review"], status="done"
    )
    tracker.add(category="job", title="Apply to Acme", tags=["urgent"], status="open")

    by_cat = tracker.list(category="task")
    assert len(by_cat) == 2
    assert all(e.category == "task" for e in by_cat)

    by_status = tracker.list(status="done")
    assert len(by_status) == 1
    assert by_status[0].title == "Review PR"

    by_tag = tracker.list(tag="dev")
    assert len(by_tag) == 2
    assert all("dev" in e.tags for e in by_tag)

    by_tag_urgent = tracker.list(tag="urgent")
    assert len(by_tag_urgent) == 1
    assert by_tag_urgent[0].category == "job"

    # Combined: category + status
    by_cat_status = tracker.list(category="task", status="open")
    assert len(by_cat_status) == 1
    assert by_cat_status[0].title == "Write tests"


# ---------------------------------------------------------------------------
# 7. follow_ups() overdue filter
# ---------------------------------------------------------------------------


def test_follow_ups_overdue_filter(workspace: Workspace) -> None:
    past1 = date(2025, 1, 1)
    past2 = date(2025, 6, 15)
    future = date(2099, 12, 31)

    tracker = Tracker(workspace)

    # Two overdue entries (due in past, next_action set)
    tracker.add(category="task", title="Overdue A", due=past1, next_action="Call back")
    tracker.add(category="task", title="Overdue B", due=past2, next_action="Follow up")

    # One future entry (next_action set but not overdue)
    tracker.add(category="task", title="Future C", due=future, next_action="Prep")

    # One entry with next_action but no due date (included in non-overdue, excluded from overdue)
    tracker.add(category="task", title="No-due D", next_action="Check in")

    overdue = tracker.follow_ups(overdue=True)
    assert len(overdue) == 2
    assert {e.title for e in overdue} == {"Overdue A", "Overdue B"}

    all_with_next_action = tracker.follow_ups(overdue=False)
    assert len(all_with_next_action) == 4


# ---------------------------------------------------------------------------
# 8. stats() counts by category and status
# ---------------------------------------------------------------------------


def test_stats_counts_by_category_and_status(workspace: Workspace) -> None:
    tracker = Tracker(workspace)

    tracker.add(category="task", title="T1", status="open")
    tracker.add(category="task", title="T2", status="open")
    tracker.add(category="task", title="T3", status="done")
    tracker.add(category="job", title="J1", status="applied")

    stats = tracker.stats()

    assert stats["total"] == 4
    assert stats["by_category"]["task"] == 3
    assert stats["by_category"]["job"] == 1
    assert stats["by_status"]["open"] == 2
    assert stats["by_status"]["done"] == 1
    assert stats["by_status"]["applied"] == 1


# ---------------------------------------------------------------------------
# 9. save() is atomic — original file unchanged if os.replace raises
# ---------------------------------------------------------------------------


def test_save_is_atomic(workspace: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Tracker(workspace)
    # Seed one entry so the tracker file exists with known content.
    tracker.add(category="task", title="Existing")
    original_text = tracker.path.read_text(encoding="utf-8")

    original_replace = os.replace

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        tracker.add(category="task", title="Should not persist")

    monkeypatch.setattr(os, "replace", original_replace)

    # Original file must be unchanged.
    assert tracker.path.read_text(encoding="utf-8") == original_text


# ---------------------------------------------------------------------------
# 10. Concurrent writes serialized by lock — all 20 entries present, IDs unique
# ---------------------------------------------------------------------------


def test_concurrent_write_serialized_by_lock(workspace: Workspace) -> None:
    errors: list[Exception] = []

    def add_entries() -> None:
        t = Tracker(workspace)
        for _ in range(10):
            try:
                t.add(category="task", title="Concurrent entry")
            except Exception as exc:
                errors.append(exc)

    thread_a = threading.Thread(target=add_entries)
    thread_b = threading.Thread(target=add_entries)
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert not errors, f"threads raised: {errors}"

    tracker = Tracker(workspace)
    entries = tracker.list(category="task")
    assert len(entries) == 20

    ids = [e.id for e in entries]
    assert len(set(ids)) == 20, "all IDs must be unique"


# ---------------------------------------------------------------------------
# 10b. R1 — concurrent append_note must not lose an append
# ---------------------------------------------------------------------------


# Module-level so multiprocessing spawn can pickle it.
def _append_note_worker(
    workspace_root: str, entry_id: str, note: str, ready: Any, go: Any
) -> None:
    ws = Workspace.discover_or_fail(override=Path(workspace_root))
    tracker = Tracker(ws)
    # Barrier both workers so they contend for the lock simultaneously — this is
    # what surfaced the lost-append bug when the read-modify-write spanned
    # outside the lock.
    ready.set()
    go.wait(timeout=10)
    tracker.update(entry_id, append_note=note)


def test_concurrent_append_note_preserves_both(workspace: Workspace) -> None:
    tracker = Tracker(workspace)
    entry = tracker.add(category="task", title="Append race")

    ready_a = multiprocessing.Event()
    ready_b = multiprocessing.Event()
    go = multiprocessing.Event()

    proc_a = multiprocessing.Process(
        target=_append_note_worker,
        args=(str(workspace.root), entry.id, "note-A", ready_a, go),
    )
    proc_b = multiprocessing.Process(
        target=_append_note_worker,
        args=(str(workspace.root), entry.id, "note-B", ready_b, go),
    )
    proc_a.start()
    proc_b.start()
    assert ready_a.wait(timeout=5)
    assert ready_b.wait(timeout=5)
    go.set()
    proc_a.join(timeout=10)
    proc_b.join(timeout=10)
    assert proc_a.exitcode == 0
    assert proc_b.exitcode == 0

    notes = Tracker(workspace).get(entry.id).notes
    lines = set(notes.splitlines())
    assert "note-A" in lines, f"note-A lost: {notes!r}"
    assert "note-B" in lines, f"note-B lost: {notes!r}"


# ---------------------------------------------------------------------------
# 11. YAML round-trip preserves datetime, tags, extras
# ---------------------------------------------------------------------------


def test_yaml_round_trip_preserves_datetime_and_extras(workspace: Workspace) -> None:
    frozen = datetime(2026, 4, 20, 14, 30, 0, tzinfo=timezone.utc)
    clock_mod.FROZEN_TIME = frozen
    try:
        tracker = Tracker(workspace)
        tracker.add(
            category="task",
            title="Round-trip test",
            due=date(2026, 5, 1),
            tags=["alpha", "beta"],
            extras={"key1": "val1", "count": "42"},
        )
    finally:
        clock_mod.FROZEN_TIME = None

    # Reload from disk
    tracker2 = Tracker(workspace)
    loaded = tracker2.load()
    assert len(loaded.entries) == 1
    e = loaded.entries[0]

    # Datetime round-trip: value must match (timezone-aware comparison)
    assert e.created_at.replace(tzinfo=timezone.utc) == frozen or e.created_at == frozen
    assert e.due == date(2026, 5, 1)
    assert e.tags == ["alpha", "beta"]
    assert e.extras["key1"] == "val1"
    assert e.extras["count"] == "42"


# ---------------------------------------------------------------------------
# 12. Empty workspace load returns empty TrackerFile
# ---------------------------------------------------------------------------


def test_empty_workspace_load_returns_empty_file(workspace: Workspace) -> None:
    tracker = Tracker(workspace)
    result = tracker.load()
    assert isinstance(result, TrackerFile)
    assert result.entries == []


# ---------------------------------------------------------------------------
# 13. Tracker._path resolves tilde output_dir via workspace.output_dir
# ---------------------------------------------------------------------------


def test_tracker_path_resolves_tilde_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tracker must use workspace.output_dir so ~/notes expands correctly."""
    import os.path as osp

    # Redirect ~ expansion so ~/notes resolves inside tmp_path.
    original_expanduser = osp.expanduser

    def fake_expanduser(p: str) -> str:
        if p.startswith("~"):
            return str(tmp_path) + p[1:]
        return original_expanduser(p)

    monkeypatch.setattr(osp, "expanduser", fake_expanduser)

    config_text = "daily_driver:\n  output_dir: ~/notes\ntracker:\n  categories:\n    task: {required: [title]}\n"
    marker = tmp_path / ".dd-config.yaml"
    marker.write_text(config_text, encoding="utf-8")
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir(exist_ok=True)

    ws = Workspace.discover_or_fail(override=tmp_path)
    tracker = Tracker(ws)

    assert tracker.path == tmp_path / "notes" / "tracker.yaml"


# ---------------------------------------------------------------------------
# 14. RECOMMENDED_STATUSES + warn-on-unknown-status
# ---------------------------------------------------------------------------


def test_recommended_statuses_constant_exposed() -> None:
    """The recommended set is the contract surface used by `help` and warnings."""
    from daily_driver.core.tracker import RECOMMENDED_STATUSES

    assert RECOMMENDED_STATUSES == (
        "open",
        "in-progress",
        "blocked",
        "done",
        "ruled-out",
    )


def _tracker_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """WARNING+ records from the tracker logger (ignores other modules' chatter)."""
    return [
        r
        for r in caplog.records
        if r.name == "daily_driver.tracker" and r.levelno >= logging.WARNING
    ]


def test_add_unknown_status_emits_warning(
    workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    tracker = Tracker(workspace)
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="task", title="weird", status="foobar")
    text = caplog.text
    assert "foobar" in text
    # The hint should reference the config knob so users know how to silence.
    assert "warn_unknown_status" in text


def test_add_recommended_status_no_warning(
    workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    tracker = Tracker(workspace)
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="task", title="okay", status="in-progress")
    assert not _tracker_warnings(caplog)


def test_add_default_status_no_warning(
    workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    """Default status is `open`; must not warn."""
    tracker = Tracker(workspace)
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="task", title="default")
    assert not _tracker_warnings(caplog)


def test_update_unknown_status_emits_warning(
    workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    tracker = Tracker(workspace)
    entry = tracker.add(category="task", title="t1")
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.update(entry.id, status="frobnicated")
    assert "frobnicated" in caplog.text


def test_warn_silenced_when_status_already_used(
    workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    """If another entry already uses this non-recommended status, suppress warning.

    Rationale: the user has clearly opted into this custom status; nagging
    twice is noise.
    """
    tracker = Tracker(workspace)
    tracker.add(category="task", title="first", status="custom")
    caplog.clear()  # the first add legitimately warns; only the second matters
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="task", title="second", status="custom")
    assert not _tracker_warnings(caplog)


def test_job_category_warns_on_generic_status(
    workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    """`category=job` uses the JobStatus lifecycle — generic statuses warn."""
    tracker = Tracker(workspace)
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="job", title="Acme", status="open")
    text = caplog.text
    assert "open" in text
    # The recommended set in the warning should list job-lifecycle values.
    assert "applied" in text


def test_job_category_accepts_job_statuses(
    workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    tracker = Tracker(workspace)
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="job", title="Acme", status="applied")
    assert not _tracker_warnings(caplog)


def test_warn_silenced_by_config_flag(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Setting tracker.warn_unknown_status: false silences the warning."""
    config_text = """\
daily_driver:
  output_dir: .
tracker:
  warn_unknown_status: false
  categories:
    task: {required: [title]}
"""
    (tmp_path / ".dd-config.yaml").write_text(config_text, encoding="utf-8")
    (tmp_path / ".daily-driver").mkdir(exist_ok=True)
    ws = Workspace.discover_or_fail(override=tmp_path)
    tracker = Tracker(ws)
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="task", title="quiet", status="weird")
    assert not _tracker_warnings(caplog)


def test_underscore_variant_of_recommended_does_not_warn(
    workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    """`ruled_out` normalizes to the recommended `ruled-out`, so no warning."""
    tracker = Tracker(workspace)
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="task", title="t", status="ruled_out")
    assert not _tracker_warnings(caplog)


def test_extra_statuses_extends_recommended_set(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A status listed in tracker.extra_statuses must not warn."""
    config_text = """\
daily_driver:
  output_dir: .
tracker:
  extra_statuses: [waiting, snoozed]
  categories:
    task: {required: [title]}
"""
    (tmp_path / ".dd-config.yaml").write_text(config_text, encoding="utf-8")
    (tmp_path / ".daily-driver").mkdir(exist_ok=True)
    ws = Workspace.discover_or_fail(override=tmp_path)
    tracker = Tracker(ws)
    with caplog.at_level(logging.WARNING, logger="daily_driver.tracker"):
        tracker.add(category="task", title="t", status="waiting")
    assert not _tracker_warnings(caplog)


# ---------------------------------------------------------------------------
# update() merges --extra keys into existing extras (does not replace)
# ---------------------------------------------------------------------------


def test_update_extras_merges_new_key(workspace: Workspace) -> None:
    """A newly supplied extras key is added alongside existing ones."""
    tracker = Tracker(workspace)
    entry = tracker.add(
        category="task", title="merge test", extras={"alpha": "1", "beta": "2"}
    )

    updated = tracker.update(entry.id, extras={"gamma": "3"})

    assert updated.extras == {"alpha": "1", "beta": "2", "gamma": "3"}


def test_update_extras_overwrites_same_key_keeps_siblings(
    workspace: Workspace,
) -> None:
    """Supplying an existing key updates it; unmentioned keys persist."""
    tracker = Tracker(workspace)
    entry = tracker.add(
        category="task", title="overwrite test", extras={"alpha": "1", "beta": "2"}
    )

    updated = tracker.update(entry.id, extras={"alpha": "99"})

    assert updated.extras == {"alpha": "99", "beta": "2"}


def test_update_without_extras_leaves_existing_untouched(
    workspace: Workspace,
) -> None:
    """An update that supplies no extras must not disturb the existing dict."""
    tracker = Tracker(workspace)
    entry = tracker.add(
        category="task", title="untouched test", extras={"alpha": "1", "beta": "2"}
    )

    updated = tracker.update(entry.id, status="done")

    assert updated.extras == {"alpha": "1", "beta": "2"}
