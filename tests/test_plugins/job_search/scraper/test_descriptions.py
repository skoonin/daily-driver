"""Tests for the descriptions.jsonl sidecar: round-trip, tolerance, naming."""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_driver.plugins.job_search.scraper.descriptions import (
    atomic_write_descriptions,
    descriptions_path,
    load_descriptions,
)


def test_descriptions_path_sits_beside_jobs_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    assert descriptions_path(csv_path) == tmp_path / "descriptions.jsonl"


def test_load_descriptions_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    assert load_descriptions(tmp_path / "jobs.csv") == {}


def test_round_trip_write_then_load(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    store = {
        "https://example.com/a": "Description A",
        "https://example.com/b": "Description B",
    }
    atomic_write_descriptions(csv_path, store)
    assert load_descriptions(csv_path) == store


def test_round_trip_preserves_unicode_and_embedded_newlines(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    text = "Senior Engineer — café team\nRequirements:\n- Python\n- Ümlaut"
    store = {"https://example.com/unicode": text}
    atomic_write_descriptions(csv_path, store)
    assert load_descriptions(csv_path) == store


def test_malformed_line_is_skipped_without_raising(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    sidecar = descriptions_path(csv_path)
    sidecar.write_text(
        '{"url": "https://example.com/ok", "text": "fine"}\n'
        "not json at all\n"
        '{"url": "https://example.com/missing-text"}\n'
        "\n"
        '{"url": "https://example.com/ok2", "text": "also fine"}\n',
        encoding="utf-8",
    )
    store = load_descriptions(csv_path)
    assert store == {
        "https://example.com/ok": "fine",
        "https://example.com/ok2": "also fine",
    }


def test_whole_file_read_failure_returns_empty_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError on read (e.g. a permissions problem) never raises -- the
    store is a cache, not the source of truth, so it must degrade to empty."""
    csv_path = tmp_path / "jobs.csv"
    sidecar = descriptions_path(csv_path)
    sidecar.write_text('{"url": "https://example.com/a", "text": "x"}\n')

    def _raise_os_error(*args: object, **kwargs: object) -> str:
        raise OSError("simulated read failure")

    monkeypatch.setattr(Path, "read_text", _raise_os_error)
    assert load_descriptions(csv_path) == {}


def test_atomic_write_leaves_no_stray_tmp_file_on_success(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    atomic_write_descriptions(csv_path, {"https://example.com/a": "text"})
    sidecar = descriptions_path(csv_path)
    assert sidecar.exists()
    assert not sidecar.with_suffix(sidecar.suffix + ".tmp").exists()
