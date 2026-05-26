from __future__ import annotations

from datetime import datetime
from pathlib import Path

from daily_driver.gathers.notes import gather_note_paths


def _make_note(output_dir: Path, date_str: str, suffix: str = "notes") -> Path:
    year, month, _ = date_str.split("-")
    dir_path = output_dir / year / month
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / f"{date_str}-{suffix}.md"
    p.write_text(f"# {date_str}\n")
    return p


def test_gather_note_paths_returns_files_in_window(tmp_path):
    _make_note(tmp_path, "2026-04-01", "plan")
    expected = _make_note(tmp_path, "2026-04-15", "notes")
    _make_note(tmp_path, "2026-04-30", "notes")

    since = datetime(2026, 4, 10)
    until = datetime(2026, 4, 20)

    result = gather_note_paths(tmp_path, since, until)

    assert result == [expected]


def test_gather_note_paths_skips_malformed_filename(tmp_path):
    expected = _make_note(tmp_path, "2026-04-15", "notes")

    # Write a file whose stem doesn't start with YYYY-MM-DD
    bad_dir = tmp_path / "2026" / "04"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "not-a-date.md").write_text("# bad\n")

    since = datetime(2026, 4, 1)
    until = datetime(2026, 4, 30)

    result = gather_note_paths(tmp_path, since, until)

    assert expected in result
    assert all("not-a-date" not in str(p) for p in result)


def test_gather_note_paths_sorted(tmp_path):
    p3 = _make_note(tmp_path, "2026-04-18", "notes")
    p1 = _make_note(tmp_path, "2026-04-11", "plan")
    p2 = _make_note(tmp_path, "2026-04-15", "notes")

    since = datetime(2026, 4, 10)
    until = datetime(2026, 4, 20)

    result = gather_note_paths(tmp_path, since, until)

    assert result == sorted([p1, p2, p3])


def test_gather_note_paths_empty_dir(tmp_path):
    since = datetime(2026, 4, 1)
    until = datetime(2026, 4, 30)

    result = gather_note_paths(tmp_path, since, until)

    assert result == []
