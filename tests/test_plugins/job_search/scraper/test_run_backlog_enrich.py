"""`jobs run` folds never-enriched pre-existing rows into the enrichment wave.

Plan phase 8 ("option A"): after scrape/dedup, pre-existing jobs.csv rows with
an empty Date Enriched and an enrich-eligible status are enriched alongside the
run's new rows. New rows keep fit-budget priority; folded rows hydrate their
description from the sidecar (cache-only, no fetching); jobs.csv row order is
preserved; the manifest reports backlog counts without inflating new_jobs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
from daily_driver.plugins.job_search.scraper.descriptions import (
    atomic_write_descriptions,
)
from tests.test_plugins.job_search.scraper import make_enriched
from tests.test_plugins.job_search.scraper.test_run_resilience import (
    _enrich_plugin,
    _read_csv,
    _scraped,
    _seed_jobs_csv,
    _serial_ctx,
)


def _preexisting_row(url: str, company: str, **overrides: Any) -> dict[str, str]:
    """A pre-existing jobs.csv row: comp set (detail fetch skips), no fit/notes,
    no Date Enriched unless overridden."""
    job = make_enriched(company=company, url=url, comp="$100k", **overrides)
    return job.to_csv_row()


def _fake_scrape_returning(jobs: list[dict[str, Any]]) -> Any:
    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    return fake_scrape


def _no_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )


def _stub_fit(monkeypatch: pytest.MonkeyPatch, fit: int = 8) -> None:
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider,
        "invoke_for",
        lambda prompt, **kw: json.dumps({"fit": fit, "notes": "scored"}),
    )


def _manifest(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))


def test_run_enriches_preexisting_unenriched_rows_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing rows with empty Date Enriched are scored during a normal run,
    keep their on-disk position, and get Date Enriched stamped. New rows still
    append at the bottom and are counted as new; folded rows are not."""
    _no_archive(monkeypatch)
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            _preexisting_row("https://old/1", "Acme"),
            _preexisting_row("https://old/2", "Bravo"),
        ],
    )
    atomic_write_descriptions(
        csv_path, {"https://old/1": "infra role", "https://old/2": "sre role"}
    )
    new = [_scraped("https://new/3", "Charlie", comp="$200k", description_text="new")]
    monkeypatch.setattr(runner, "run_all_scrapers", _fake_scrape_returning(new))
    _stub_fit(monkeypatch)

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0

    rows = _read_csv(csv_path)
    # Position preserved: folded rows stay put, the new row appends at the end.
    assert [r["Company"] for r in rows] == ["Acme", "Bravo", "Charlie"]
    assert all(r["Fit"] == "8" for r in rows), rows
    assert all(r["Date Enriched"] for r in rows)

    manifest = _manifest(tmp_path)
    assert manifest["new_jobs"] == 1  # folded rows are NOT new
    assert manifest["backlog_enriched"] == 2
    assert manifest["backlog_remaining"] == 0


def test_run_skips_enriched_and_ineligible_preexisting_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows with a Date Enriched stamp or a non-eligible status are left alone."""
    import datetime as dt

    _no_archive(monkeypatch)
    csv_path = tmp_path / "jobs.csv"
    stamped = _preexisting_row(
        "https://old/1",
        "Done",
        fit=9,
        notes="already",
        date_enriched=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    )
    skipped = _preexisting_row("https://old/2", "Skipped", status="skipped")
    eligible = _preexisting_row("https://old/3", "Eligible")
    _seed_jobs_csv(csv_path, [stamped, skipped, eligible])
    atomic_write_descriptions(
        csv_path,
        {
            "https://old/1": "desc",
            "https://old/2": "desc",
            "https://old/3": "desc",
        },
    )
    monkeypatch.setattr(runner, "run_all_scrapers", _fake_scrape_returning([]))
    _stub_fit(monkeypatch, fit=7)

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0

    rows = {r["Company"]: r for r in _read_csv(csv_path)}
    assert rows["Done"]["Fit"] == "9"  # untouched
    assert rows["Skipped"]["Fit"] == ""
    assert rows["Skipped"]["Date Enriched"] == ""
    assert rows["Eligible"]["Fit"] == "7"
    assert rows["Eligible"]["Date Enriched"] != ""
    assert _manifest(tmp_path)["backlog_enriched"] == 1


def test_backlog_consumes_only_leftover_budget_new_rows_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With max_enrich_fit=1 and one new row, the new row takes the budget and
    the folded row is left un-scored, reported as remaining backlog."""
    _no_archive(monkeypatch)
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [_preexisting_row("https://old/1", "Acme")])
    atomic_write_descriptions(csv_path, {"https://old/1": "desc"})
    new = [_scraped("https://new/2", "Bravo", comp="$200k", description_text="new")]
    monkeypatch.setattr(runner, "run_all_scrapers", _fake_scrape_returning(new))
    _stub_fit(monkeypatch)

    rc = runner.run(
        _enrich_plugin(budget=1),
        tmp_path,
        tmp_path,
        ai=_serial_ctx(budget=1).ai,
        no_enrich=False,
    )
    assert rc == 0

    rows = {r["Company"]: r for r in _read_csv(csv_path)}
    assert rows["Bravo"]["Fit"] == "8"  # the new row won the budget
    assert rows["Acme"]["Fit"] == ""
    assert rows["Acme"]["Date Enriched"] == ""

    manifest = _manifest(tmp_path)
    assert manifest["backlog_enriched"] == 0
    assert manifest["backlog_remaining"] == 1


def test_no_enrich_leaves_backlog_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--no-enrich skips the backlog fold entirely (unchanged behavior)."""
    _no_archive(monkeypatch)
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [_preexisting_row("https://old/1", "Acme")])
    atomic_write_descriptions(csv_path, {"https://old/1": "desc"})
    monkeypatch.setattr(runner, "run_all_scrapers", _fake_scrape_returning([]))

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        no_enrich=True,
    )
    assert rc == 0
    rows = _read_csv(csv_path)
    assert rows[0]["Fit"] == ""
    assert rows[0]["Date Enriched"] == ""


def test_reseen_and_folded_row_keeps_fresh_date_last_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row that is re-seen by the scrape AND backlog-enriched in the same run
    gets BOTH updates: rescan's Date Last Seen bump must land after the folded
    overwrite (which carries the stale date captured at selection)."""
    import datetime as dt

    _no_archive(monkeypatch)
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [_preexisting_row("https://old/1", "Acme", date_found=dt.date(2026, 6, 1))],
    )
    atomic_write_descriptions(csv_path, {"https://old/1": "desc"})
    # The scrape re-returns the known job -> re-sighting, not a new row.
    reseen = [_scraped("https://old/1", "Acme", comp="$100k")]
    monkeypatch.setattr(runner, "run_all_scrapers", _fake_scrape_returning(reseen))
    _stub_fit(monkeypatch)

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0

    (row,) = _read_csv(csv_path)
    assert row["Fit"] == "8"
    assert row["Date Enriched"] != ""
    assert row["Date Last Seen"] == dt.date.today().isoformat()


def test_backlog_row_without_description_left_unscored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backlog row with no sidecar description never enters the wave (no
    wasted fetch): Fit stays empty and Date Enriched is NOT stamped, and it is
    excluded from backlog_remaining (a re-scrape must heal it first)."""
    _no_archive(monkeypatch)
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            _preexisting_row("https://old/1", "NoDesc"),
            _preexisting_row("https://old/2", "HasDesc"),
        ],
    )
    atomic_write_descriptions(csv_path, {"https://old/2": "desc"})
    monkeypatch.setattr(runner, "run_all_scrapers", _fake_scrape_returning([]))
    _stub_fit(monkeypatch, fit=6)

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0

    rows = {r["Company"]: r for r in _read_csv(csv_path)}
    assert rows["HasDesc"]["Fit"] == "6"
    assert rows["NoDesc"]["Fit"] == ""
    assert rows["NoDesc"]["Date Enriched"] == ""
    manifest = _manifest(tmp_path)
    assert manifest["backlog_enriched"] == 1
    assert manifest["backlog_remaining"] == 0  # unscorable != remaining


def test_legacy_scored_rows_not_counted_as_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row scored before the Date Enriched column existed (fit+notes filled,
    empty timestamp) is NOT selected -- the wave would skip it anyway, and
    counting it would inflate backlog_remaining forever."""
    _no_archive(monkeypatch)
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [_preexisting_row("https://old/1", "Legacy", fit=7, notes="pre-column")],
    )
    atomic_write_descriptions(csv_path, {"https://old/1": "desc"})
    monkeypatch.setattr(runner, "run_all_scrapers", _fake_scrape_returning([]))
    _stub_fit(monkeypatch)

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0

    (row,) = _read_csv(csv_path)
    assert row["Fit"] == "7"  # untouched
    manifest = _manifest(tmp_path)
    assert manifest["backlog_enriched"] == 0
    assert manifest["backlog_remaining"] == 0


def test_no_enrich_run_persists_scraped_descriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh --no-enrich run (no re-sightings) must still flush the sidecar:
    scrape-captured descriptions are what a later plain run's backlog wave (or
    backfill) scores from. Regression: the final flush used to fire only on
    enrichment or re-sightings, silently dropping every scraped description."""
    from daily_driver.plugins.job_search.scraper.descriptions import (
        load_descriptions,
    )

    _no_archive(monkeypatch)
    new = [
        _scraped("https://new/1", "Acme", comp="$200k", description_text="scraped body")
    ]
    monkeypatch.setattr(runner, "run_all_scrapers", _fake_scrape_returning(new))

    rc = runner.run(
        JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
        tmp_path,
        tmp_path,
        no_enrich=True,
    )
    assert rc == 0
    assert load_descriptions(tmp_path / "jobs.csv") == {"https://new/1": "scraped body"}


def test_folded_update_preserves_concurrent_hand_edit(tmp_path: Path) -> None:
    """_apply_folded_updates writes only enrichment-owned cells, so a Status
    hand-edited on disk mid-run survives the write-back; duplicate-identity
    rows all receive the update."""
    sink = runner._JobSink(
        csv_path=tmp_path / "jobs.csv",
        lock_path=tmp_path / ".lock",
        header=list(CANONICAL_HEADER),
        known_urls=set(),
        known_keys=set(),
        plugin=JobSearchPlugin.model_validate({"scraper": {"enabled": True}}),
    )
    enriched = make_enriched(company="Acme", url="https://old/1", fit=8, notes="scored")
    sink.folded_rows = [enriched]
    # Two physical rows sharing the identity; the user flipped Status on disk
    # mid-run -- the folded write-back must keep it.
    row_a = make_enriched(company="Acme", url="https://old/1").to_csv_row()
    row_a["Status"] = "applied"
    row_b = make_enriched(company="Acme", url="https://old/1").to_csv_row()
    leading = [row_a, row_b]

    sink._apply_folded_updates(leading)

    assert row_a["Status"] == "applied"  # hand-edit survives
    assert row_a["Fit"] == "8" and row_b["Fit"] == "8"  # both duplicates updated
    assert row_a["Notes"] == "scored"
