"""Tests for core/manifest.py — SHA-256 manifest for package-managed files."""

from __future__ import annotations

import json
from pathlib import Path

from daily_driver.core import manifest


def test_load_returns_empty_when_missing(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    assert manifest.load(state_dir) == {}


def test_load_returns_empty_on_corrupt_json(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    (state_dir / "manifest.json").write_text("not json", encoding="utf-8")
    assert manifest.load(state_dir) == {}


def test_load_returns_empty_on_wrong_version(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    (state_dir / "manifest.json").write_text(
        json.dumps({"version": 99, "files": {"a.md": "abc"}}), encoding="utf-8"
    )
    assert manifest.load(state_dir) == {}


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    data = {".claude/commands/daily-driver/foo.md": "deadbeef"}
    manifest.save(state_dir, data)
    assert manifest.load(state_dir) == data


def test_save_is_atomic(tmp_path: Path) -> None:
    """No .tmp file left behind after successful save."""
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    manifest.save(state_dir, {"x": "y"})
    assert not list(state_dir.glob("*.tmp")), "no temp files must remain"


def test_is_user_edited_false_when_file_missing(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    assert not manifest.is_user_edited(tmp_path, state_dir, "nonexistent.md")


def test_is_user_edited_false_when_no_record(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    target = tmp_path / "some.md"
    target.write_text("hello", encoding="utf-8")
    # No manifest entry for this file — treated as package-owned (no edits to preserve).
    assert not manifest.is_user_edited(tmp_path, state_dir, "some.md")


def test_is_user_edited_false_when_sha_matches(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    target = tmp_path / "some.md"
    target.write_text("hello", encoding="utf-8")
    manifest.record(state_dir, tmp_path, [target])
    assert not manifest.is_user_edited(tmp_path, state_dir, "some.md")


def test_is_user_edited_true_when_sha_differs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    target = tmp_path / "some.md"
    target.write_text("original", encoding="utf-8")
    manifest.record(state_dir, tmp_path, [target])
    # Simulate user edit.
    target.write_text("user modified content", encoding="utf-8")
    assert manifest.is_user_edited(tmp_path, state_dir, "some.md")


def test_record_preserves_existing_entries(tmp_path: Path) -> None:
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("aaa", encoding="utf-8")
    b.write_text("bbb", encoding="utf-8")
    manifest.record(state_dir, tmp_path, [a])
    manifest.record(state_dir, tmp_path, [b])
    stored = manifest.load(state_dir)
    assert "a.md" in stored
    assert "b.md" in stored
