"""Driver tests for the modern ``jobs backfill`` path (runner.run_backfill).

Backfill shares the run-side enrichment machinery: detail pages, the overlapped
product + fit/notes coordinator, periodic flushes, and the ollama preflight. It
enriches only rows with empty fields, bounds both LLM budgets with ``--limit``,
and under ``--dry-run`` makes zero LLM calls and zero writes while reporting the
per-phase would-enrich counts. All status lines route through Console (no bare
stdout print) — closing audit L-4.
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
    product: str = "",
    gd: str = "",
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
        "Date Last Seen": "2026-04-01",
        "Date Applied": "",
        "Link": link,
        "Product/Purpose": product,
        "GD Rating": gd,
        "Source": "remoteok",
    }


def _plugin() -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
            "enrichment": {
                "max_enrich_companies": 50,
                "max_enrich_fit": 50,
                "detail_delay_seconds": 0,
            },
        }
    )


def _stub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op detail enrichment: leaves rows unchanged, reports zero fetched."""
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def fake_detail(jobs: list[Any], ctx: Any, *, progress: Any = None) -> Any:
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
                product="SaaS",
                gd="4.0",
            ),
        ],
    )
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    seen_fit_companies: list[str] = []

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        # The fit plan would target only empty-Fit rows; emulate by enriching
        # every empty-field row in place and recording which companies it touched.
        for i, j in enumerate(jobs):
            if not j.fit:
                jobs[i] = j.with_updates(
                    fit=7, notes="filled", product="P", gd_rating="3.9"
                )
                seen_fit_companies.append(j.company)
        fit_prog = kwargs.get("fit_progress")
        prod_prog = kwargs.get("product_progress")
        if fit_prog is not None:
            fit_prog(len(jobs))
        if prod_prog is not None:
            prod_prog(len(jobs))
        return (
            jobs,
            {"enriched": 1, "skipped_cached": 0, "failed": 0},
            {"enriched": 1, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    rows = _read_jobs_csv(csv_path)
    by_company = {r["Company"]: r for r in rows}
    assert by_company["Empty"]["Fit"] == "7"
    assert by_company["Empty"]["Notes"] == "filled"
    # Fully-filled row untouched.
    assert by_company["Full"]["Notes"] == "done"
    assert seen_fit_companies == ["Empty"]


def test_backfill_limit_bounds_both_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--limit N caps BOTH product_budget and fit_budget at N."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [_row(company=f"C{i}", link=f"https://example.com/{i}") for i in range(5)],
    )
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    captured: dict[str, int] = {}

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        captured["product_budget"] = kwargs.get("product_budget")
        captured["fit_budget"] = kwargs.get("fit_budget")
        return (
            jobs,
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path, limit=3)

    assert captured["product_budget"] == 3
    assert captured["fit_budget"] == 3


def test_backfill_no_limit_uses_config_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --limit, both budgets pass 0 (the config-cap sentinel)."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    captured: dict[str, int] = {}

    def fake_concurrent(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        captured["product_budget"] = kwargs.get("product_budget")
        captured["fit_budget"] = kwargs.get("fit_budget")
        return (
            jobs,
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path, limit=None)

    assert captured["product_budget"] == 0
    assert captured["fit_budget"] == 0


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
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

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
                product="P",
                gd="4.0",
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
    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", boom_concurrent
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path, dry_run=True)

    # No writes and no backup taken under dry-run.
    assert csv_path.read_bytes() == before
    assert not (tmp_path / "backups").exists()

    err = capsys.readouterr().err
    # One row needs all four; the report names the per-phase counts.
    assert "1" in err
    assert "Product" in err and "Fit" in err


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
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

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
                product="P",
                gd="4.0",
            )
        ],
    )

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("no enrichment when all rows are filled")

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", boom)
    monkeypatch.setattr(enrichment_pkg, "enrich_product_and_fit_concurrently", boom)

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
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )


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
            jobs[i] = j.with_updates(fit=7, notes="n", product="P", gd_rating="4.0")
        return (
            jobs,
            {"enriched": len(jobs), "skipped_cached": 0, "failed": 0},
            {
                "enriched": len(jobs),
                "skipped_budget": 0,
                "skipped_no_desc": 0,
                "failed": 0,
            },
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

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
            jobs[i] = j.with_updates(fit=6, notes="n", product="P", gd_rating="4.0")
        return (
            jobs,
            {"enriched": len(jobs), "skipped_cached": 0, "failed": 0},
            {
                "enriched": len(jobs),
                "skipped_budget": 0,
                "skipped_no_desc": 0,
                "failed": 0,
            },
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

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
        jobs[0] = jobs[0].with_updates(fit=7, notes="n", product="P", gd_rating="4.0")
        flush = kwargs.get("flush")
        if callable(flush):
            flush()  # the periodic hook (flush_periodic) -> degrades on OSError
        return (
            jobs,
            {"enriched": 1, "skipped_cached": 0, "failed": 0},
            {"enriched": 1, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

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
        jobs[0] = jobs[0].with_updates(fit=7, notes="n", product="P", gd_rating="4.0")
        return (
            jobs,
            {"enriched": 1, "skipped_cached": 0, "failed": 0},
            {"enriched": 1, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment, "enrich_product_and_fit_concurrently", fake_concurrent
    )

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
        jobs[0] = jobs[0].with_updates(fit=7, notes="n", product="P", gd_rating="4.0")
        state["interrupted"] = True
        raise KeyboardInterrupt

    monkeypatch.setattr(enrichment, "enrich_product_and_fit_concurrently", interrupting)

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
        jobs[0] = jobs[0].with_updates(fit=7, notes="n", product="P", gd_rating="4.0")
        return (
            jobs,
            {"enriched": 1, "skipped_cached": 0, "failed": 0},
            {"enriched": 1, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    monkeypatch.setattr(
        enrichment, "enrich_product_and_fit_concurrently", fake_concurrent
    )

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    backups = list((tmp_path / "backups").glob("jobs.csv.bak.*"))
    assert len(backups) == 1
    # The backup holds the ORIGINAL (pre-enrichment) Fit cell.
    assert _read_jobs_csv(backups[0])[0]["Fit"] == ""
    assert _read_jobs_csv(csv_path)[0]["Fit"] == "7"


# --- Finding 8: dry-run truth (combined fit/notes + toggle-aware product/gd) --


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
    """With product/gd enrichment off, those axes report zero need."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])

    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
            "enrichment": {
                "enrich_product": False,
                "enrich_gd_rating": False,
                "detail_delay_seconds": 0,
            },
        }
    )

    runner.run_backfill(plugin, csv_path, tmp_path, dry_run=True)

    err = capsys.readouterr().err
    assert "0 need Product" in err
    assert "0 need GD" in err
