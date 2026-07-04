"""Board-diff closure: rows absent from a successful full listing get closed.

Plan phase 3, PR-3a. Guards under test: raw pre-role-filter enumerations,
only-enumerated-boards scoping, the MANDATORY 2-consecutive-miss threshold,
miss-clearing on re-appearance, and triage statuses being untouchable.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search.scraper import closure, runner
from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
from tests.test_plugins.job_search.scraper.test_run_resilience import (
    _read_csv,
    _us_remote_plugin,
)

_TODAY = dt.date.today().isoformat()


def _row(url: str, company: str, status: str = "found", **cells: str) -> dict[str, str]:
    base = {
        "Status": status,
        "Company": company,
        "Role": "SRE",
        "Link": url,
        "Date Found": "2026-06-01",
        "Source": "Greenhouse (acme)",
    }
    base.update(cells)
    return base


# ── decide_closures (pure) ───────────────────────────────────────────────────


def test_first_miss_counts_but_does_not_close() -> None:
    rows = [_row("https://x/1", "Acme")]
    closures, misses, stats = closure.decide_closures(
        rows, {"Greenhouse (acme)": set()}, {}, _TODAY
    )
    assert closures == {}
    assert misses == {"https://x/1": 1}
    assert stats == {"closed": 0, "pending_misses": 1}


def test_second_consecutive_miss_closes() -> None:
    rows = [_row("https://x/1", "Acme", Notes="great fit")]
    closures, misses, stats = closure.decide_closures(
        rows, {"Greenhouse (acme)": set()}, {"https://x/1": 1}, _TODAY
    )
    assert misses == {}  # ledger entry consumed
    assert stats["closed"] == 1
    updates = closures["https://x/1"]
    assert updates["Status"] == "closed"
    assert updates["Date Closed"] == _TODAY
    assert updates["Notes"] == f"great fit | [closed: board-diff {_TODAY}]"


def test_reappearance_clears_the_miss_counter() -> None:
    rows = [_row("https://x/1", "Acme")]
    closures, misses, _stats = closure.decide_closures(
        rows,
        {"Greenhouse (acme)": {"https://x/1"}},
        {"https://x/1": 1},
        _TODAY,
    )
    assert closures == {}
    assert misses == {}  # blip healed; the 2-miss clock restarts


def test_unenumerated_board_never_closes() -> None:
    """A board removed from config or whose fetch failed records no
    enumeration -- its rows are 'not checked', never 'gone'."""
    rows = [_row("https://x/1", "Acme")]
    closures, misses, _stats = closure.decide_closures(
        rows, {"Greenhouse (other)": set()}, {"https://x/1": 1}, _TODAY
    )
    assert closures == {}
    assert misses == {"https://x/1": 1}  # untouched, not incremented


def test_triaged_rows_are_untouchable() -> None:
    """Closure only annotates the untriaged inbox; the user's statuses win."""
    rows = [
        _row("https://x/1", "Applied", status="applied"),
        _row("https://x/2", "Skipped", status="skipped"),
        _row("https://x/3", "Blank", status=""),
    ]
    closures, misses, _stats = closure.decide_closures(
        rows, {"Greenhouse (acme)": set()}, {}, _TODAY
    )
    assert closures == {} and misses == {}


# ── miss ledger sidecar ──────────────────────────────────────────────────────


def test_miss_ledger_round_trips_and_tolerates_corruption(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    closure.save_misses(csv_path, {"https://x/1": 1})
    assert closure.load_misses(csv_path) == {"https://x/1": 1}
    closure.misses_path(csv_path).write_text("{not json", encoding="utf-8")
    assert closure.load_misses(csv_path) == {}  # reset, never abort


# ── end-to-end through runner.run ────────────────────────────────────────────


def _seed(csv_path: Path, rows: list[dict[str, str]]) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _fake_scrape_with_enumeration(urls: set[str]) -> Any:
    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        ctx.record_enumeration("Greenhouse (acme)", urls)
        return [], [], []

    return fake_scrape


def test_run_closes_after_two_runs_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two consecutive absent-from-listing runs close the row on disk (also
    under --no-enrich: closure is a scrape fact); run 1 only records a miss."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    csv_path = tmp_path / "jobs.csv"
    _seed(csv_path, [_row("https://x/gone", "Acme"), _row("https://x/live", "Bravo")])
    monkeypatch.setattr(
        runner, "run_all_scrapers", _fake_scrape_with_enumeration({"https://x/live"})
    )

    assert runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True) == 0
    rows = {r["Company"]: r for r in _read_csv(csv_path)}
    assert rows["Acme"]["Status"] == "found"  # first miss only
    ledger = json.loads(closure.misses_path(csv_path).read_text())
    assert ledger == {"https://x/gone": 1}

    assert runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True) == 0
    rows = {r["Company"]: r for r in _read_csv(csv_path)}
    assert rows["Acme"]["Status"] == "closed"
    assert rows["Acme"]["Date Closed"] == _TODAY
    assert "[closed: board-diff" in rows["Acme"]["Notes"]
    assert rows["Bravo"]["Status"] == "found"  # present in listing, untouched
    assert json.loads(closure.misses_path(csv_path).read_text()) == {}


def test_dry_run_never_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    csv_path = tmp_path / "jobs.csv"
    _seed(csv_path, [_row("https://x/gone", "Acme")])
    original = csv_path.read_bytes()
    monkeypatch.setattr(
        runner, "run_all_scrapers", _fake_scrape_with_enumeration(set())
    )

    assert runner.run(_us_remote_plugin(), tmp_path, tmp_path, dry_run=True) == 0
    assert csv_path.read_bytes() == original
    assert not closure.misses_path(csv_path).exists()
