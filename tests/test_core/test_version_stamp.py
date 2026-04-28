from __future__ import annotations

from pathlib import Path

from daily_driver.core import version_stamp


def test_read_missing_file_returns_none(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    assert version_stamp.read(state_dir) is None


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    version_stamp.write(state_dir, "1.2.3")
    assert version_stamp.read(state_dir) == "1.2.3"


def test_write_strips_whitespace_on_read(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    stamp = state_dir / version_stamp.STAMP_FILENAME
    stamp.write_text("  0.1.0.dev0\n", encoding="utf-8")
    assert version_stamp.read(state_dir) == "0.1.0.dev0"


def test_write_is_atomic(tmp_path: Path) -> None:
    """No partial write: the .tmp file must not be present after a successful write."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    version_stamp.write(state_dir, "1.0.0")
    tmp_file = state_dir / (version_stamp.STAMP_FILENAME + ".tmp")
    assert not tmp_file.exists()


def test_write_creates_state_dir(tmp_path: Path) -> None:
    state_dir = tmp_path / "nested" / "state"
    assert not state_dir.exists()
    version_stamp.write(state_dir, "0.2.0")
    assert (state_dir / version_stamp.STAMP_FILENAME).exists()


def test_is_drifted_when_stamp_missing(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    assert version_stamp.is_drifted(state_dir, "1.0.0") is True


def test_is_drifted_when_version_mismatch(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    version_stamp.write(state_dir, "1.0.0")
    assert version_stamp.is_drifted(state_dir, "1.0.1") is True


def test_is_not_drifted_when_version_matches(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    version_stamp.write(state_dir, "1.0.0")
    assert version_stamp.is_drifted(state_dir, "1.0.0") is False


def test_write_overwrites_existing_stamp(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    version_stamp.write(state_dir, "1.0.0")
    version_stamp.write(state_dir, "2.0.0")
    assert version_stamp.read(state_dir) == "2.0.0"
