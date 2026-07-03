"""Driver tests for the modern ``jobs backfill`` path (runner.run_backfill).

Backfill shares the run-side enrichment machinery: detail pages, the fit/notes
pass, periodic flushes, and the ollama preflight. It enriches only rows with
empty fields, bounds the LLM budget with ``--limit``, and under ``--dry-run``
makes zero LLM calls and zero writes while reporting the would-enrich count. All
status lines route through Console (no bare stdout print) — closing audit L-4.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER


def _write_jobs_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CANONICAL_HEADER,
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_jobs_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _row(
    *,
    company: str,
    link: str,
    status: str = "found",
    fit: str = "",
    notes: str = "",
) -> dict[str, str]:
    return {
        "Status": status,
        "Notes": notes,
        "Company": company,
        "Location": "Remote",
        "Role": "SRE",
        "Fit": fit,
        "Comp": "",
        "Date Found": "2026-04-01",
        "Date Verified": "2026-04-01",
        "Date Applied": "",
        "Link": link,
        "Source": "remoteok",
    }


def _plugin(max_enrich_fit: int = 50) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
            "enrichment": {
                "max_enrich_fit": max_enrich_fit,
                "detail_delay_seconds": 0,
            },
        }
    )


def _stub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op detail enrichment: leaves rows unchanged, reports zero fetched."""
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_detail(
        jobs: list[Any],
        ctx: Any,
        *,
        progress: Any = None,
        capture_descriptions: bool = True,
    ) -> Any:
        if progress is not None:
            progress(len(jobs))
        return jobs, {
            "total": len(jobs),
            "fetched": 0,
            "enriched": 0,
            "failed": 0,
            "skipped": len(jobs),
            "skip_reasons": {},
        }

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", fake_detail)


def test_backfill_enriches_only_empty_field_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully-filled row is excluded; only the empty-field row is enriched."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [
            _row(company="Empty", link="https://example.com/empty"),
            _row(
                company="Full",
                link="https://example.com/full",
                fit="8",
                notes="done",
            ),
        ],
    )
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    seen_fit_companies: list[str] = []

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        # The fit plan would target only empty-Fit rows; emulate by enriching
        # every empty-field row in place and recording which companies it touched.
        for i, j in enumerate(jobs):
            if not j.fit:
                jobs[i] = j.with_updates(fit=7, notes="filled")
                seen_fit_companies.append(j.company)
        progress = kwargs.get("progress")
        if progress is not None:
            progress(len(jobs))
        return (
            jobs,
            {"enriched": 1, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    rows = _read_jobs_csv(csv_path)
    by_company = {r["Company"]: r for r in rows}
    assert by_company["Empty"]["Fit"] == "7"
    assert by_company["Empty"]["Notes"] == "filled"
    # Fully-filled row untouched.
    assert by_company["Full"]["Notes"] == "done"
    assert seen_fit_companies == ["Empty"]


def test_backfill_warns_when_rows_have_no_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A no_description count from the fit/notes pass surfaces a Console.warning."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="NoDesc", link="https://example.com/x")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        progress = kwargs.get("progress")
        if progress is not None:
            progress(len(jobs))
        return jobs, {
            "enriched": 0,
            "skipped_budget": 0,
            "no_description": 2,
            "failed": 0,
        }

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    err = capsys.readouterr().err
    assert "2 job(s) had no cached description" in err


def test_backfill_hydrates_blank_description_without_overwriting_non_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backfill fills a blank ``description_text`` from the sidecar and leaves
    a row that already carries one untouched (fill-missing-only)."""
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
    )
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob

    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [
            _row(company="Blank", link="https://example.com/blank"),
            _row(company="Prefilled", link="https://example.com/prefilled"),
        ],
    )
    atomic_write_descriptions(
        csv_path,
        {
            "https://example.com/blank": "Hydrated from sidecar.",
            "https://example.com/prefilled": "Sidecar text that must lose.",
        },
    )
    _stub_detail(monkeypatch)

    # jobs.csv carries no description column, so from_csv_row always yields a
    # blank description_text; simulate a row that already has one in memory
    # (e.g. a future source that sets it before hydration runs) by patching
    # the classmethod every EnrichedJob.from_csv_row call goes through.
    original_from_csv_row = EnrichedJob.from_csv_row

    def fake_from_csv_row(cls: type[EnrichedJob], row: dict[str, str]) -> EnrichedJob:
        job: EnrichedJob = original_from_csv_row(row)
        if job.company == "Prefilled":
            job = job.with_updates(description_text="Already-fetched description.")
        return job

    monkeypatch.setattr(EnrichedJob, "from_csv_row", classmethod(fake_from_csv_row))

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    seen_descriptions: dict[str, str] = {}

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        for job in jobs:
            seen_descriptions[job.company] = job.description_text
        progress = kwargs.get("progress")
        if progress is not None:
            progress(len(jobs))
        return (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    assert seen_descriptions["Blank"] == "Hydrated from sidecar."
    assert seen_descriptions["Prefilled"] == "Already-fetched description."


def test_backfill_is_description_cache_only_and_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Backfill never fetches or writes descriptions: it drives the detail phase
    with ``capture_descriptions=False``, writes nothing to the sidecar, and warns
    that an uncached row is left un-enriched. (The dedicated LinkedIn fetcher was
    removed; descriptions are captured only during ``jobs run``.)"""
    from daily_driver.plugins.job_search.scraper.descriptions import load_descriptions

    csv_path = tmp_path / "jobs.csv"
    # No descriptions.jsonl seeded -> the row has no cached description.
    _write_jobs_csv(csv_path, [_row(company="NoDesc", link="https://example.com/x")])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    captured: dict[str, Any] = {}

    def fake_detail(
        jobs: list[Any],
        ctx: Any,
        *,
        progress: Any = None,
        capture_descriptions: bool = True,
    ) -> Any:
        captured["capture_descriptions"] = capture_descriptions
        if progress is not None:
            progress(len(jobs))
        return jobs, {
            "total": len(jobs),
            "fetched": 0,
            "enriched": 0,
            "failed": 0,
            "skipped": len(jobs),
            "skip_reasons": {},
        }

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", fake_detail)

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        # Model the real no-description skip: no LLM call, no write, counted.
        progress = kwargs.get("progress")
        if progress is not None:
            progress(len(jobs))
        return jobs, {
            "enriched": 0,
            "skipped_budget": 0,
            "no_description": 1,
            "failed": 0,
        }

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    # Backfill told the detail phase not to capture descriptions.
    assert captured["capture_descriptions"] is False
    # No description was written to the sidecar.
    assert load_descriptions(csv_path) == {}
    # The user is told the row has no cached description.
    assert "no cached description" in capsys.readouterr().err


def test_backfill_gcs_orphaned_descriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backfill drops sidecar entries whose URL is no longer in jobs.csv."""
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
        load_descriptions,
    )

    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="Live", link="https://example.com/live")])
    atomic_write_descriptions(
        csv_path,
        {
            "https://example.com/live": "keep",
            "https://example.com/orphan": "drop",
        },
    )
    _stub_detail(monkeypatch)
    _stub_concurrent_noop(monkeypatch)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    assert load_descriptions(csv_path) == {"https://example.com/live": "keep"}


def test_backfill_gc_survives_a_dirty_sidecar_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GC'd orphan must stay gone even when enrichment triggers a real sidecar
    rewrite. Enrichment that sets a new description marks the sink's description
    store dirty, so its flush rewrites descriptions.jsonl -- if the in-memory
    store were not pruned alongside the on-disk GC, that flush would resurrect
    the orphan. This exercises the ``store = {...}`` prune at the GC call site."""
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
        load_descriptions,
    )

    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="Live", link="https://example.com/live")])
    atomic_write_descriptions(
        csv_path,
        {"https://example.com/live": "existing", "https://example.com/orphan": "drop"},
    )
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        # Change the row (dirties jobs.csv) AND set a new description (dirties the
        # sidecar), forcing the flush path that could resurrect the orphan.
        for i, job in enumerate(jobs):
            jobs[i] = job.with_updates(
                fit=7, notes="n", description_text="fetched body"
            )
        progress = kwargs.get("progress")
        if progress is not None:
            progress(len(jobs))
        return jobs, {"enriched": len(jobs), "skipped_budget": 0, "failed": 0}

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    store = load_descriptions(csv_path)
    assert "https://example.com/orphan" not in store
    assert store["https://example.com/live"] == "fetched body"


def test_backfill_dry_run_previews_gc_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run reports the would-drop count but leaves the sidecar untouched."""
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
        load_descriptions,
    )

    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="Live", link="https://example.com/live")])
    store = {
        "https://example.com/live": "keep",
        "https://example.com/orphan": "drop",
    }
    atomic_write_descriptions(csv_path, store)

    runner.run_backfill(_plugin(), csv_path, tmp_path, dry_run=True)

    err = capsys.readouterr().err
    assert "would clean up 1 orphaned description(s)" in err
    assert load_descriptions(csv_path) == store


def test_backfill_limit_bounds_fit_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--limit N caps the fit budget at N."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [_row(company=f"C{i}", link=f"https://example.com/{i}") for i in range(5)],
    )
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    captured: dict[str, int] = {}

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        captured["budget"] = kwargs.get("budget")
        return (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path, limit=3)

    assert captured["budget"] == 3


def test_backfill_no_limit_uses_config_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --limit, the budget passes None (the config-cap sentinel)."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    captured: dict[str, int] = {}

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        captured["budget"] = kwargs.get("budget")
        return (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path, limit=None)

    assert captured["budget"] is None


def test_backfill_dry_run_notes_config_cap_when_needs_exceed_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the backlog exceeds the config caps, the dry-run report says the
    pass is capped -- otherwise the needs counts read as if caps were ignored."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [_row(company=f"C{i}", link=f"https://example.com/{i}") for i in range(3)],
    )
    plugin = _plugin(max_enrich_fit=2)
    runner.run_backfill(plugin, csv_path, tmp_path, dry_run=True)
    err = capsys.readouterr().err
    assert "capped at 2" in err
    assert "run backfill again" in err


def test_backfill_flushes_periodically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The periodic flush hook reaches the concurrent coordinator (flush kwarg)."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    saw_flush: list[bool] = []

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        flush = kwargs.get("flush")
        saw_flush.append(callable(flush))
        # Exercise it once: a periodic flush must succeed (write jobs.csv).
        if callable(flush):
            flush()
        return (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_concurrent)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    assert saw_flush == [True]
    # The mid-flush leaves jobs.csv readable and intact.
    assert len(_read_jobs_csv(csv_path)) == 1


def test_backfill_dry_run_no_calls_no_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--dry-run: zero LLM/detail calls, no writes, prints would-enrich counts."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [
            _row(company="A", link="https://example.com/a"),
            _row(
                company="B",
                link="https://example.com/b",
                fit="8",
                notes="x",
            ),
        ],
    )
    before = csv_path.read_bytes()

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def boom_detail(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("dry-run must not call detail enrichment")

    def boom_concurrent(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("dry-run must not call LLM enrichment")

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", boom_detail)
    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", boom_concurrent)

    runner.run_backfill(_plugin(), csv_path, tmp_path, dry_run=True)

    # No writes and no backup taken under dry-run.
    assert csv_path.read_bytes() == before
    assert not (tmp_path / "backups").exists()

    err = capsys.readouterr().err
    # One row needs Fit/Notes; the report names the count.
    assert "1" in err
    assert "Fit" in err


def test_backfill_status_lines_route_through_console(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Completion line routes through Console (stderr), not a bare stdout print.

    The old backfill emitted its status with bare ``print`` to stdout (audit
    L-4). The modern driver must route through the Console facade, so stdout
    stays clean and Console.success carries the message.
    """
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.core.console import Console
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        return (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_concurrent)

    success_calls: list[str] = []
    monkeypatch.setattr(
        Console,
        "success",
        classmethod(lambda cls, msg: success_calls.append(msg)),
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    assert success_calls, "backfill completion must route through Console.success"
    assert any("ackfill" in m for m in success_calls)
    # Nothing about the backfill status leaks to bare stdout.
    assert "Backfill" not in capsys.readouterr().out


def test_backfill_dry_run_returns_summary_dict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_backfill returns a completion summary the CLI can wrap in --json."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [
            _row(company="A", link="https://example.com/a"),
            _row(company="B", link="https://example.com/b", fit="8", notes="x"),
        ],
    )

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_job_details",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("no calls")),
    )

    summary = runner.run_backfill(_plugin(), csv_path, tmp_path, dry_run=True)

    assert summary["dry_run"] is True
    # Both rows are active; one needs Fit/Notes, the other is already filled.
    assert summary["rows"] == 2
    assert summary["needs_before"] == 1
    assert summary["needs_after"] == 1
    assert summary["enriched"] == 0
    assert summary["skipped"] == 0
    assert summary["elapsed_seconds"] is None


def test_backfill_emit_json_suppresses_console_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emit_json=True returns the summary but routes no human line through Console."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.core.console import Console
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_fit_and_notes",
        lambda jobs, ctx, **kw: (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        ),
    )

    success_calls: list[str] = []
    monkeypatch.setattr(
        Console,
        "success",
        classmethod(lambda cls, msg: success_calls.append(msg)),
    )

    summary = runner.run_backfill(_plugin(), csv_path, tmp_path, emit_json=True)

    assert summary["dry_run"] is False
    assert not success_calls, "emit_json must suppress the human completion line"


def test_backfill_all_filled_reports_nothing_to_do(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fully-enriched file: no enrichment calls, an info line, no backup."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [
            _row(
                company="C",
                link="https://example.com/c",
                fit="8",
                notes="x",
            )
        ],
    )

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("no enrichment when all rows are filled")

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", boom)
    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", boom)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    assert not (tmp_path / "backups").exists()
    err = capsys.readouterr().err
    assert "nothing to backfill" in err.lower()


def _stub_concurrent_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op LLM enrichment: changes no row, zero stats."""
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        return (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_concurrent)


# --- Finding 1: read inside the lock -----------------------------------------


def test_backfill_reads_jobs_inside_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """jobs.csv must be read AFTER the sentinel lock is acquired, not before.

    Recording the lock-acquire and the file-read order proves a concurrent run's
    append cannot land in a read-then-lock window and be clobbered by the rewrite.
    """
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)
    _stub_concurrent_noop(monkeypatch)

    events: list[str] = []

    from contextlib import contextmanager

    from daily_driver.plugins.job_search.scraper import csv_io

    real_read_rows = csv_io.read_rows

    def traced_read_rows(path: Path) -> Any:
        events.append("read")
        return real_read_rows(path)

    @contextmanager
    def traced_lock(path: Path, **_kw: Any):
        events.append("lock")
        yield

    monkeypatch.setattr(runner, "file_lock", traced_lock)
    # run_backfill imports read_rows from csv_io inside the function; patch there.
    monkeypatch.setattr(csv_io, "read_rows", traced_read_rows)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    # The first lock must precede the first in-lock read.
    assert "lock" in events and "read" in events
    assert events.index("lock") < events.index("read"), events


# --- Finding 2: per-row extras survive empty / duplicate Links ---------------


def test_backfill_extras_kept_on_empty_link_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row with a blank Link keeps its own hand-added column through a rewrite."""
    csv_path = tmp_path / "jobs.csv"
    header = CANONICAL_HEADER + ["Priority"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        # Empty-Link row that needs enrichment, plus a normal one.
        r0 = _row(company="NoLink", link="")
        r0["Priority"] = "high"
        r1 = _row(company="Has", link="https://example.com/h")
        r1["Priority"] = "low"
        w.writerow({**r0, "Priority": "high"})
        w.writerow({**r1, "Priority": "low"})

    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        for i, j in enumerate(jobs):
            jobs[i] = j.with_updates(fit=7, notes="n")
        return (
            jobs,
            {
                "enriched": len(jobs),
                "skipped_budget": 0,
                "failed": 0,
            },
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_concurrent)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    rows = _read_jobs_csv(csv_path)
    by_company = {r["Company"]: r for r in rows}
    assert by_company["NoLink"]["Priority"] == "high"
    assert by_company["Has"]["Priority"] == "low"
    # Enrichment still applied to the empty-Link row.
    assert by_company["NoLink"]["Fit"] == "7"


def test_backfill_extras_distinct_for_duplicate_link_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two rows sharing a Link keep their DISTINCT extras (no cross-contamination)."""
    csv_path = tmp_path / "jobs.csv"
    header = CANONICAL_HEADER + ["Priority"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        a = _row(company="Alpha", link="https://dup/1")
        b = _row(company="Beta", link="https://dup/1")
        w.writerow({**a, "Priority": "alpha-pri"})
        w.writerow({**b, "Priority": "beta-pri"})

    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        for i, j in enumerate(jobs):
            jobs[i] = j.with_updates(fit=6, notes="n")
        return (
            jobs,
            {
                "enriched": len(jobs),
                "skipped_budget": 0,
                "failed": 0,
            },
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_concurrent)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    rows = _read_jobs_csv(csv_path)
    by_company = {r["Company"]: r for r in rows}
    assert by_company["Alpha"]["Priority"] == "alpha-pri"
    assert by_company["Beta"]["Priority"] == "beta-pri"


# --- Finding 4: persistence-degraded parity ----------------------------------


def test_backfill_warns_when_periodic_flush_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A degraded periodic flush surfaces a warning before the final save."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        jobs[0] = jobs[0].with_updates(fit=7, notes="n")
        flush = kwargs.get("flush")
        if callable(flush):
            flush()  # the periodic hook (flush_periodic) -> degrades on OSError
        return (
            jobs,
            {"enriched": 1, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_concurrent)

    # Make the FIRST write (the periodic flush) fail, later writes succeed.
    from daily_driver.plugins.job_search.scraper import csv_io

    real_write = csv_io.atomic_write_rows
    calls = {"n": 0}

    def flaky_write(path: Path, header: list[str], rows: list[Any]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full (simulated)")
        real_write(path, header, rows)

    monkeypatch.setattr(csv_io, "atomic_write_rows", flaky_write)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    err = capsys.readouterr().err
    assert "periodic saves failed" in err
    # The retried final save persisted the change.
    assert _read_jobs_csv(csv_path)[0]["Fit"] == "7"


# --- Finding 5: final-flush failure names the backup -------------------------


def test_backfill_final_flush_failure_names_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An OSError from the final flush is reported with the backup path, re-raised."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import csv_io, enrichment

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        jobs[0] = jobs[0].with_updates(fit=7, notes="n")
        return (
            jobs,
            {"enriched": 1, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment, "enrich_fit_and_notes", fake_concurrent)

    def always_fail(path: Path, header: list[str], rows: list[Any]) -> None:
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(csv_io, "atomic_write_rows", always_fail)

    with pytest.raises(OSError):
        runner.run_backfill(_plugin(), csv_path, tmp_path)

    err = capsys.readouterr().err
    assert "final save failed" in err.lower()
    # A backup was taken (lazy, just before the failing write) and is named.
    backups = list((tmp_path / "backups").glob("jobs.csv.bak.*"))
    assert backups and backups[0].name in err


# --- Finding 6: interrupt-path masking ---------------------------------------


def test_backfill_interrupt_teardown_flush_error_preserves_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-OSError from the teardown flush must not mask the KeyboardInterrupt."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment

    state = {"interrupted": False}

    def interrupting(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        jobs[0] = jobs[0].with_updates(fit=7, notes="n")
        state["interrupted"] = True
        raise KeyboardInterrupt

    monkeypatch.setattr(enrichment, "enrich_fit_and_notes", interrupting)

    # The teardown flush (only AFTER the interrupt) blows up with a RuntimeError,
    # e.g. a second Ctrl-C race. The post-detail periodic flush before the
    # interrupt must still succeed, so guard on the interrupt flag.
    real_flush = runner._JobSink.flush

    def maybe_boom_flush(self: Any) -> None:
        if state["interrupted"]:
            raise RuntimeError("teardown explosion")
        real_flush(self)

    monkeypatch.setattr(runner._JobSink, "flush", maybe_boom_flush)

    with pytest.raises(KeyboardInterrupt):
        runner.run_backfill(_plugin(), csv_path, tmp_path)


# --- Finding 7: no-change churn (lazy backup, skip unchanged rewrite) ---------


def test_backfill_no_change_leaves_file_and_writes_no_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eligible rows but enrichment changes nothing (ollama down): no write, no backup."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    before = csv_path.read_bytes()
    before_mtime = csv_path.stat().st_mtime_ns

    _stub_detail(monkeypatch)  # detail changes nothing
    _stub_concurrent_noop(monkeypatch)  # LLM changes nothing

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    assert csv_path.read_bytes() == before
    assert csv_path.stat().st_mtime_ns == before_mtime
    assert not (tmp_path / "backups").exists()


def test_backfill_backup_taken_before_first_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real change takes exactly one backup, captured before the write."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        jobs[0] = jobs[0].with_updates(fit=7, notes="n")
        return (
            jobs,
            {"enriched": 1, "skipped_budget": 0, "failed": 0},
        )

    monkeypatch.setattr(enrichment, "enrich_fit_and_notes", fake_concurrent)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    backups = list((tmp_path / "backups").glob("jobs.csv.bak.*"))
    assert len(backups) == 1
    # The backup holds the ORIGINAL (pre-enrichment) Fit cell.
    assert _read_jobs_csv(backups[0])[0]["Fit"] == ""
    assert _read_jobs_csv(csv_path)[0]["Fit"] == "7"


# --- Finding 8: dry-run truth (combined fit/notes, toggle-aware) ------------


def test_backfill_dry_run_counts_fit_notes_once_combined(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fit-filled / notes-empty row is counted once under the combined predicate."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [_row(company="C", link="https://example.com/c", fit="8", notes="")],
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path, dry_run=True)

    err = capsys.readouterr().err
    # One Fit/Notes need (the combined predicate), reported as such.
    assert "1 need Fit/Notes" in err


def test_backfill_dry_run_respects_disabled_toggles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With either fit/notes toggle off, the fit/notes axis reports zero need.

    The fit pass refuses to run unless BOTH enrich_fit and enrich_notes are on
    (audit M3), so the dry-run count must be gated the same way -- otherwise it
    over-reports need and the start-of-run short-circuit never fires.
    """
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])

    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
            "enrichment": {
                "enrich_fit": False,
                "detail_delay_seconds": 0,
            },
        }
    )

    runner.run_backfill(plugin, csv_path, tmp_path, dry_run=True)

    err = capsys.readouterr().err
    assert "0 need Fit/Notes" in err


# --- Visible status canonicalization (review #1) -----------------------------


def test_backfill_reports_status_canonicalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rewrite that fixes a Status spelling surfaces one Console.info notice.

    Backfill lifts every row through from_csv_row (which normalizes spelling),
    silently rewriting rows the user did not ask to touch — so the canonical-
    ization must be visible. The ``found`` row makes the backfill proceed (only
    found/pending rows are eligible); the ``Ruled_Out`` row is not enriched but
    is still canonicalized by the rewrite.
    """
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [
            _row(company="C", link="https://example.com/c", status="Ruled_Out"),
            _row(company="D", link="https://example.com/d", status="found"),
        ],
    )
    _stub_detail(monkeypatch)
    _stub_concurrent_noop(monkeypatch)

    from daily_driver.core.console import Console

    info_calls: list[str] = []
    monkeypatch.setattr(
        Console, "info", classmethod(lambda cls, msg: info_calls.append(msg))
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    notices = [m for m in info_calls if "Canonicalized" in m]
    assert len(notices) == 1
    assert "1 status spelling" in notices[0]
    assert "Ruled_Out -> ruled-out" in notices[0]
    # The spelling fix is persisted.
    assert _read_jobs_csv(csv_path)[0]["Status"] == "ruled-out"


def test_backfill_no_canonicalization_notice_when_already_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No notice when every Status is already canonical."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path, [_row(company="C", link="https://example.com/c", status="found")]
    )
    _stub_detail(monkeypatch)
    _stub_concurrent_noop(monkeypatch)

    from daily_driver.core.console import Console

    info_calls: list[str] = []
    monkeypatch.setattr(
        Console, "info", classmethod(lambda cls, msg: info_calls.append(msg))
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    assert not [m for m in info_calls if "Canonicalized" in m]
