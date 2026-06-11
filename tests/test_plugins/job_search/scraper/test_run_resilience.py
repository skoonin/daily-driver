"""Run-resilience: per-source append, enrichment flush, overlap, SIGTERM/manifest.

The durable record (jobs.csv) is the checkpoint: each source's rows are appended
as it completes, enrichment updates rows in place with periodic flushes, and a
crash/interrupt loses at most one source or one flush window. These tests inject
failures via stubs (no real signals where avoidable) so the resilience claims are
deterministic.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from daily_driver.core.config_models import AIConfig
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched


def _scraped(url: str, company: str, role: str = "SRE", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "url": url,
        "company": company,
        "role": role,
        "source": extra.pop("source", "remoteok"),
        "location": extra.pop("location", "Remote"),
        "comp": "",
        "date_found": "2026-06-10",
    }
    base.update(extra)
    return base


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _us_remote_plugin() -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "roles": ["Engineer"],
            "locations": {"countries": ["US"], "remote": True},
        }
    )


# ── Stage 1: per-source append ───────────────────────────────────────────────


def test_append_jobs_for_source_writes_and_updates_dedup(tmp_path: Path) -> None:
    """A sink appends one source's deduped/filtered/lifted rows and grows its
    known-url set so a later source dedups against them (cross-source)."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CANONICAL_HEADER)

    sink = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=CANONICAL_HEADER,
        known_urls=set(),
        known_keys=set(),
        plugin=_us_remote_plugin(),
    )

    counts_a = sink.append_source(
        "src_a", [_scraped("https://a/1", "Acme"), _scraped("https://a/2", "Bravo")]
    )
    assert counts_a["new"] == 2
    # The Apple wave (later source) must dedup against what src_a already wrote.
    counts_b = sink.append_source(
        "src_b", [_scraped("https://a/1", "Acme"), _scraped("https://b/3", "Charlie")]
    )
    assert counts_b["new"] == 1
    assert counts_b["known"] == 1

    rows = _read_csv(csv_path)
    assert [r["Company"] for r in rows] == ["Acme", "Bravo", "Charlie"]
    # Master row list holds every appended job for the enrichment pass.
    assert len(sink.rows) == 3


def test_append_source_location_filters_and_drops_urlless(tmp_path: Path) -> None:
    """Per-source append mirrors run()'s funnel: location-skip and url-less drop."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CANONICAL_HEADER)
    sink = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=CANONICAL_HEADER,
        known_urls=set(),
        known_keys=set(),
        plugin=runner_us_only_plugin(),
    )
    counts = sink.append_source(
        "src",
        [
            _scraped("https://a/1", "Acme", location="Seattle, United States"),
            _scraped("https://a/2", "Bravo", location="Berlin, Germany"),
            _scraped("", "NoUrl", location="Seattle, United States"),
        ],
    )
    assert counts["new"] == 1
    assert counts["loc_skip"] == 1
    rows = _read_csv(csv_path)
    assert [r["Company"] for r in rows] == ["Acme"]


def runner_us_only_plugin() -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "roles": ["Engineer"],
            "locations": {"countries": ["US"], "remote": False},
        }
    )


def test_run_appends_per_source_then_crash_keeps_first_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after source A appended must leave A's rows in jobs.csv.

    The scraper orchestrator is stubbed to append source A through the sink
    callback, then raise before source B — proving rows survive a mid-scrape
    crash without a final batch append.
    """
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        assert on_source_result is not None
        on_source_result("src_a", [_scraped("https://a/1", "Acme")])
        raise RuntimeError("scraper crashed mid-run")

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    with pytest.raises(RuntimeError):
        runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)

    rows = _read_csv(tmp_path / "jobs.csv")
    assert [r["Company"] for r in rows] == ["Acme"]

    # A non-KI crash mid-scrape must still leave an honest manifest, not the
    # previous run's "complete" one (F1).
    import json

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["interrupted"] is True
    assert manifest["phase_reached"] == "scraping"
    assert manifest["new_jobs"] == 1


def test_dry_run_appends_nothing_per_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run keeps the in-memory single-pass behavior: no writes at all."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    results = [("remoteok", [_scraped("https://a/1", "Acme")])]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", [_scraped("https://a/1", "Acme")])
        return [_scraped("https://a/1", "Acme")], [], results

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, dry_run=True)
    assert rc == 0
    # No csv written at all under dry-run.
    assert (
        not (tmp_path / "jobs.csv").exists() or _read_csv(tmp_path / "jobs.csv") == []
    )


# ── Stage 2: enrichment in-place flush ───────────────────────────────────────


def _enrich_plugin(budget: int = 50) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_companies": budget,
                "max_enrich_fit": budget,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            },
        }
    )


def _serial_ctx(budget: int = 50) -> ScrapeContext:
    # max_parallel=1 -> serial provider path: deterministic ordering, no threads.
    return ScrapeContext(
        plugin=_enrich_plugin(budget),
        ai=AIConfig.model_validate({"claude": {"max_parallel": 1}}),
    )


def test_concurrent_enrichment_flushes_every_n_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coordinator calls the flush hook every ``flush_every`` applied results.

    With 7 fit jobs and flush_every=3, flush fires after results 3 and 6 inside
    the loop, then once on phase completion by the caller -- but the coordinator
    itself triggers at the 3-result boundaries.
    """
    from daily_driver.integrations import ai_provider
    from daily_driver.plugins.job_search.scraper import enrichment

    monkeypatch.setattr(
        ai_provider,
        "invoke_for",
        lambda prompt, **kw: '{"fit": 6, "notes": "ok"}',
    )
    jobs = [make_enriched(company=f"Co{i}", url=f"https://x/{i}") for i in range(7)]
    flush_calls: list[int] = []
    enrichment.enrich_product_and_fit_concurrently(
        jobs,
        _serial_ctx(),
        flush=lambda: flush_calls.append(1),
        flush_every=3,
    )
    # 7 fit results -> flush at 3 and 6 (the company pass writes products too,
    # but the fit pass alone guarantees at least the two boundary flushes).
    assert len(flush_calls) >= 2


def test_run_flushes_enrichment_progress_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After enrichment, run() rewrites jobs.csv so Fit/Notes land on disk."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    # comp set -> detail enricher skips the page fetch (no real network).
    jobs = [
        _scraped("https://x/1", "Acme", comp="$200k"),
        _scraped("https://x/2", "Bravo", comp="$200k"),
    ]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda prompt, **kw: '{"fit": 8, "notes": "great"}'
    )

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0
    rows = _read_csv(tmp_path / "jobs.csv")
    assert [r["Company"] for r in rows] == ["Acme", "Bravo"]
    assert all(r["Fit"] == "8" for r in rows)


def test_run_interrupt_mid_enrichment_flushes_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A KeyboardInterrupt mid-enrichment leaves the flushed rows on disk.

    A serial provider applies fit results in order, one per call; the stub
    raises on the second call. run() must flush the partial progress before the
    interrupt propagates, so the first row's Fit survives. Product enrichment is
    off so only the fit pass runs (its serial path applies each result as the
    call settles, before the next fetch).
    """
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_fit": 50,
                "enrich_product": False,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            },
        }
    )
    jobs = [_scraped(f"https://x/{i}", f"Co{i}", comp="$200k") for i in range(4)]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    from daily_driver.integrations import ai_provider

    calls = [0]

    def fake_invoke(prompt: str, **kw: Any) -> str:
        calls[0] += 1
        if calls[0] >= 2:
            raise KeyboardInterrupt
        return '{"fit": 7, "notes": "first"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    with pytest.raises(KeyboardInterrupt):
        runner.run(plugin, tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False)

    rows = _read_csv(tmp_path / "jobs.csv")
    # All rows still present (appended during scraping); at least one enriched
    # row survived the flush-on-interrupt.
    assert len(rows) == 4
    assert any(r["Fit"] == "7" for r in rows)


# ── Stage 3: scrape/enrich overlap with shared budget ────────────────────────


def _overlap_plugin(budget: int) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_companies": budget,
                "max_enrich_fit": budget,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            },
        }
    )


def _overlap_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    budget: int,
    phase1: list[dict[str, Any]],
    apple: list[dict[str, Any]],
    wave1_fit_attempted: set[str],
    wave1_companies_attempted: set[str],
) -> list[dict[str, Any]]:
    """Drive run() through a two-wave overlap with a coordinator stub that records
    each wave's (n, budgets, exclusions) and reports the given wave-1 attempted
    identities, mimicking the real coordinator's attempted out-param."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    def fake_scrape(
        ctx: Any,
        *_a: Any,
        on_source_result: Any = None,
        on_phase1_done: Any = None,
        **_kw: Any,
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", phase1)
        if on_phase1_done is not None:
            on_phase1_done(True)  # wave-1 enrichment starts here
        if on_source_result is not None:
            on_source_result("apple", apple)
        return phase1 + apple, [], [("remoteok", phase1), ("apple", apple)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    waves: list[dict[str, Any]] = []
    call = [0]

    def fake_concurrent(
        jobs: list[Any],
        ctx: Any,
        *,
        product_budget: int = 0,
        fit_budget: int = 0,
        product_progress: Any = None,
        fit_progress: Any = None,
        flush: Any = None,
        flush_every: int = 25,
        exclude_fit_urls: frozenset[str] = frozenset(),
        exclude_companies: frozenset[str] = frozenset(),
        attempted: dict[str, set[str]] | None = None,
        on_product_planned: Any = None,
        on_fit_planned: Any = None,
    ) -> Any:
        call[0] += 1
        eligible = [
            j for j in jobs if j.url not in exclude_fit_urls and not (j.fit and j.notes)
        ]
        waves.append(
            {
                "n": len(jobs),
                "fit_budget": fit_budget,
                "product_budget": product_budget,
                "exclude_fit_urls": set(exclude_fit_urls),
                "exclude_companies": set(exclude_companies),
                "eligible": len(eligible),
            }
        )
        if call[0] == 1 and attempted is not None:
            # Mimic the real coordinator filling the attempted out-param.
            attempted["fit_urls"] = set(wave1_fit_attempted)
            attempted["product_companies"] = set(wave1_companies_attempted)
        return (
            jobs,
            {"enriched": len(eligible), "skipped_cached": 0, "failed": 0},
            {
                "enriched": len(eligible),
                "skipped_budget": 0,
                "skipped_no_desc": 0,
                "failed": 0,
            },
        )

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

    rc = runner.run(
        _overlap_plugin(budget=budget),
        tmp_path,
        tmp_path,
        ai=_serial_ctx().ai,
        no_enrich=False,
    )
    assert rc == 0
    return waves


def test_overlap_two_waves_share_fit_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase-1 rows enrich in wave 1; Apple rows in wave 2. The fit budget is a
    shared running total: wave 1 attempts 7 jobs, so wave 2 caps at the
    remaining 3. Wave 2 also EXCLUDES the wave-1 rows (no retry/double-charge)."""
    phase1_urls = {f"https://p1/{i}" for i in range(7)}
    waves = _overlap_run(
        monkeypatch,
        tmp_path,
        budget=10,
        phase1=[_scraped(f"https://p1/{i}", f"P1Co{i}", comp="$x") for i in range(7)],
        apple=[_scraped(f"https://ap/{i}", f"ApCo{i}", comp="$x") for i in range(5)],
        wave1_fit_attempted=phase1_urls,
        wave1_companies_attempted={f"P1Co{i}" for i in range(7)},
    )
    assert len(waves) == 2
    assert waves[0]["n"] == 7
    assert waves[0]["fit_budget"] in (None, 10)  # wave 1 gets the full config budget
    # Wave 2 sees the whole 12-row list but excludes the 7 wave-1 URLs and caps
    # its fit budget at 10 - 7 = 3 (the shared running total).
    assert waves[1]["n"] == 12
    assert waves[1]["fit_budget"] == 3
    assert waves[1]["exclude_fit_urls"] == phase1_urls
    assert waves[1]["eligible"] == 5  # only the Apple rows remain eligible


def test_company_phase_labeled_glassdoor_when_product_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With enrich_product=false the company pass runs gd-only; the phase row
    must say so, or the run reads as if the product toggle were ignored."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    jobs = [_scraped("https://x/1", "Acme", comp="$200k")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(ai_provider, "invoke_for", lambda *a, **k: "4.2")
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "enrich_product": False,
                "enrich_gd_rating": True,
                "enrich_fit": False,
                "enrich_notes": False,
                "enrich_timeout": 5,
            },
        }
    )
    rc = runner.run(plugin, tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False)
    assert rc == 0
    err = capsys.readouterr().err
    assert "Glassdoor ratings" in err
    assert "Company products" not in err


def test_disabled_passes_render_no_bars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pass disabled by config gets NO phase row at all -- a pinned bar with
    a placeholder total for work that never runs reads as a stuck toggle
    (owner-observed: "Glassdoor ratings 0/1875" with both company toggles off)."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    jobs = [_scraped("https://x/1", "Acme", comp="$200k")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda *a, **k: '{"fit": 7, "notes": "n"}'
    )
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "enrich_product": False,
                "enrich_gd_rating": False,
                "enrich_fit": True,
                "enrich_notes": True,
                "enrich_timeout": 5,
            },
        }
    )
    rc = runner.run(plugin, tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False)
    assert rc == 0
    err = capsys.readouterr().err
    assert "Fit and notes" in err
    assert "Company products" not in err
    assert "Glassdoor ratings" not in err


def test_overlap_wave2_budget_exhausted_gets_zero_not_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When wave 1 exhausts the shared budget exactly, wave 2's budgets must be
    0 (no further spend), not a phantom minimum of 1 that overshoots the caps."""
    phase1_urls = {f"https://p1/{i}" for i in range(7)}
    waves = _overlap_run(
        monkeypatch,
        tmp_path,
        budget=7,
        phase1=[_scraped(f"https://p1/{i}", f"P1Co{i}", comp="$x") for i in range(7)],
        apple=[_scraped(f"https://ap/{i}", f"ApCo{i}", comp="$x") for i in range(3)],
        wave1_fit_attempted=phase1_urls,
        wave1_companies_attempted={f"P1Co{i}" for i in range(7)},
    )
    assert len(waves) == 2
    assert waves[1]["fit_budget"] == 0
    assert waves[1]["product_budget"] == 0


def test_overlap_failed_wave1_row_not_reattempted_in_wave2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row ATTEMPTED in wave 1 -- even if it failed (no fit/notes written) -- is
    excluded from wave 2, so it is neither retried nor double-charged. Backfill
    is the retry path."""
    phase1_urls = {f"https://p1/{i}" for i in range(4)}
    waves = _overlap_run(
        monkeypatch,
        tmp_path,
        budget=10,
        phase1=[_scraped(f"https://p1/{i}", f"P1Co{i}", comp="$x") for i in range(4)],
        apple=[_scraped(f"https://ap/{i}", f"ApCo{i}", comp="$x") for i in range(3)],
        # All 4 phase-1 rows were attempted (say 1 failed) -> all excluded.
        wave1_fit_attempted=phase1_urls,
        wave1_companies_attempted={f"P1Co{i}" for i in range(4)},
    )
    # Wave 2 excludes all 4 attempted rows (incl. the failed one) -> 3 eligible.
    assert waves[1]["exclude_fit_urls"] == phase1_urls
    assert waves[1]["eligible"] == 3
    # Budget reduced by the 4 attempted (not re-charged): 10 - 4 = 6.
    assert waves[1]["fit_budget"] == 6
    assert waves[1]["product_budget"] == 6


# ── Stage 4: SIGTERM, manifest fields, status recovery line ──────────────────


def test_manifest_records_phase_reached_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean run records phase_reached=complete and interrupted=false."""
    import json

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    jobs = [_scraped("https://x/1", "Acme", comp="$x")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda prompt, **kw: '{"fit": 5, "notes": "ok"}'
    )

    runner.run(_enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai)
    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["phase_reached"] == "complete"
    assert manifest["interrupted"] is False


def test_manifest_records_interrupted_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted run writes the manifest with interrupted=true and the
    phase it had reached, so jobs status can surface a recovery line."""
    import json

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_fit": 50,
                "enrich_product": False,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            },
        }
    )
    jobs = [_scraped(f"https://x/{i}", f"Co{i}", comp="$x") for i in range(4)]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    calls = [0]

    def fake_invoke(prompt: str, **kw: Any) -> str:
        calls[0] += 1
        if calls[0] >= 2:
            raise KeyboardInterrupt
        return '{"fit": 7, "notes": "first"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    with pytest.raises(KeyboardInterrupt):
        runner.run(plugin, tmp_path, tmp_path, ai=_serial_ctx().ai)

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["interrupted"] is True
    assert manifest["phase_reached"] in ("detail", "enrichment")


def test_status_prints_recovery_line_when_interrupted(tmp_path: Path) -> None:
    """jobs status surfaces a recovery line for an interrupted last run."""
    import json

    from daily_driver.plugins.job_search.scraper_status import build_status

    (tmp_path / "jobs-last-run.json").write_text(
        json.dumps(
            {
                "started_at": "2026-06-10T00:00:00+00:00",
                "interrupted": True,
                "phase_reached": "enrichment",
                "new_jobs": 12,
            }
        ),
        encoding="utf-8",
    )
    status = build_status(tmp_path)
    assert status["last_run"]["interrupted"] is True
    assert status["last_run"]["phase_reached"] == "enrichment"


# ── Review fixes: manifest on all exits, atomicity, persistence failures ─────


def _enrich_plugin_no_product() -> JobSearchPlugin:
    # fit-only so the serial fit pass applies results incrementally (one per call).
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_fit": 50,
                "enrich_product": False,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            },
        }
    )


def test_manifest_written_when_interrupted_during_scraping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C during the SCRAPE phase (before enrichment) still writes an
    interrupted manifest at phase=scraping (F1) -- the window the old code left
    showing the previous run's complete manifest."""
    import json

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", [_scraped("https://a/1", "Acme")])
        raise KeyboardInterrupt  # interrupted mid-scrape, before enrichment

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    with pytest.raises(KeyboardInterrupt):
        runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=False)

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["interrupted"] is True
    assert manifest["phase_reached"] == "scraping"
    # Source A's row was appended before the interrupt -> on disk and counted.
    assert manifest["new_jobs"] == 1
    assert [r["Company"] for r in _read_csv(tmp_path / "jobs.csv")] == ["Acme"]


def test_csv_init_failure_overwrites_stale_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The csv-init OSError path writes a fresh interrupted manifest, not leaving
    a prior run's complete one (F1)."""
    import json

    # Seed a stale "complete" manifest from a prior good run.
    (tmp_path / "jobs-last-run.json").write_text(
        json.dumps({"phase_reached": "complete", "interrupted": False, "new_jobs": 9}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    real_open = open

    def boom_open(path: Any, *a: Any, **k: Any) -> Any:
        if str(path).endswith("jobs.csv") and "w" in (a[0] if a else k.get("mode", "")):
            raise OSError("disk full")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", boom_open)

    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path)
    assert rc == 1
    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["interrupted"] is True
    assert manifest["phase_reached"] == "scraping"
    assert manifest["new_jobs"] == 0


def test_append_failure_isolates_source_keeps_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An append OSError for source B marks B failed and lets A + C land; no
    phantom dedup state for B's rows (F5)."""
    from daily_driver.plugins.job_search.scraper import csv_io

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    real_append = csv_io.append_jobs_typed

    def flaky_append(csv_path: Any, jobs: list[Any], header: Any) -> int:
        # Fail only B's append (its single row's company is "Bravo").
        if jobs and jobs[0].company == "Bravo":
            from daily_driver.plugins.job_search.scraper.runner import ScraperError

            raise ScraperError("disk error on B")
        return real_append(csv_path, jobs, header)

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.scraper.csv_io.append_jobs_typed",
        flaky_append,
    )

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        on_source_result("src_a", [_scraped("https://a/1", "Acme")])
        on_source_result("src_b", [_scraped("https://b/1", "Bravo")])
        on_source_result("src_c", [_scraped("https://c/1", "Charlie")])
        return [], [], [("src_a", []), ("src_b", []), ("src_c", [])]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)

    rows = _read_csv(tmp_path / "jobs.csv")
    # A and C persisted; B did not.
    assert sorted(r["Company"] for r in rows) == ["Acme", "Charlie"]
    # B is recorded as a failed source (exit 1).
    assert rc == 1
    import json

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert "src_b" in manifest["sources_failed"]
    # No phantom dedup: B's url is NOT in the sink's known set, so a re-scrape
    # would re-offer it. Re-running the same sources appends B again cleanly.
    # (Assert via a second append through a fresh sink seeded from the csv.)
    known_urls, _known_keys, header = csv_io.load_existing_jobs(tmp_path / "jobs.csv")
    assert "https://b/1" not in known_urls


def test_append_and_flush_no_duplicate_rows_under_interleave(tmp_path: Path) -> None:
    """A flush forced to interleave between an append's two steps must not
    duplicate or drop rows (F2): the whole append is one critical section."""
    import threading

    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        load_existing_jobs,
    )

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CANONICAL_HEADER)

    sink = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=CANONICAL_HEADER,
        known_urls=set(),
        known_keys=set(),
        plugin=_us_remote_plugin(),
    )
    # Seed one row so the flush has content to (re)write.
    sink.append_source("p1", [_scraped("https://p1/0", "P1")])

    # A flusher thread hammers flush() while the main thread appends the Apple
    # rows. With the single-critical-section discipline, no flush can land
    # between an append's list-extend and its file-write, so the file never
    # gains a duplicate or loses a row.
    stop = threading.Event()

    def flusher() -> None:
        while not stop.is_set():
            sink.flush()

    t = threading.Thread(target=flusher, daemon=True)
    t.start()
    for i in range(20):
        sink.append_source("apple", [_scraped(f"https://ap/{i}", f"Ap{i}")])
    stop.set()
    t.join()
    sink.flush()  # final consistent rewrite

    rows = _read_csv(csv_path)
    urls = [r["Link"] for r in rows]
    assert len(urls) == len(set(urls)), f"duplicate rows on disk: {urls}"
    assert len(rows) == 21  # 1 seed + 20 apple, none dropped
    known_urls, _k, _h = load_existing_jobs(csv_path)
    assert len(known_urls) == 21


def test_flush_preserves_preexisting_rows(tmp_path: Path) -> None:
    """A run-path flush rewrites the whole file, so rows already in jobs.csv
    before the run (including hand-added columns) must be carried through —
    not replaced by only this run's appended rows."""
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        load_existing_jobs,
        read_rows,
    )

    csv_path = tmp_path / "jobs.csv"
    header = CANONICAL_HEADER + ["Priority"]
    old_rows = [
        {
            "Status": "applied",
            "Company": "OldCo",
            "Role": "SRE",
            "Link": "https://old/1",
            "Priority": "high",
        },
        {
            "Status": "new",
            "Company": "OldCo2",
            "Role": "SRE",
            "Link": "https://old/2",
            "Priority": "",
        },
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(old_rows)

    # Mirror run()'s setup: dedup state + header from the file, pre-existing
    # rows captured for flush carry-through.
    known_urls, known_keys, file_header = load_existing_jobs(csv_path)
    _, preexisting = read_rows(csv_path)
    sink = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=file_header,
        known_urls=known_urls,
        known_keys=known_keys,
        plugin=_us_remote_plugin(),
        preexisting_rows=preexisting,
    )
    sink.append_source("remoteok", [_scraped("https://new/1", "NewCo")])
    sink.flush()

    rows = _read_csv(csv_path)
    assert [r["Company"] for r in rows] == ["OldCo", "OldCo2", "NewCo"]
    # Hand-edited cells on the pre-existing rows are untouched.
    assert rows[0]["Status"] == "applied"
    assert rows[0]["Priority"] == "high"
    assert rows[2]["Priority"] == ""


def test_run_enrichment_flush_preserves_preexisting_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An enriching run against a populated jobs.csv must keep every
    pre-existing row through the enrichment flushes (regression: the flush
    rewrote the file from only this run's rows, wiping the rest)."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CANONICAL_HEADER)
        w.writeheader()
        w.writerow(
            {
                "Status": "applied",
                "Company": "OldCo",
                "Role": "SRE",
                "Fit": "9",
                "Link": "https://old/1",
            }
        )

    jobs = [_scraped("https://x/1", "Acme", comp="$200k")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda prompt, **kw: '{"fit": 8, "notes": "great"}'
    )

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0
    rows = _read_csv(csv_path)
    assert [r["Company"] for r in rows] == ["OldCo", "Acme"]
    # The pre-existing row's cells are untouched: status and fit as written.
    assert rows[0]["Status"] == "applied"
    assert rows[0]["Fit"] == "9"
    assert rows[1]["Fit"] == "8"


def test_run_warns_on_ragged_preexisting_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A pre-existing row with MORE cells than the header loses its overflow
    on the whole-file rewrite (DictWriter extrasaction="ignore"); the run must
    warn about the trim instead of dropping the cells silently."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CANONICAL_HEADER)
        ok_row = [""] * len(CANONICAL_HEADER)
        ok_row[CANONICAL_HEADER.index("Company")] = "FineCo"
        ok_row[CANONICAL_HEADER.index("Link")] = "https://fine/1"
        w.writerow(ok_row)
        ragged_row = [""] * len(CANONICAL_HEADER)
        ragged_row[CANONICAL_HEADER.index("Company")] = "RaggedCo"
        ragged_row[CANONICAL_HEADER.index("Link")] = "https://ragged/1"
        w.writerow(ragged_row + ["OVERFLOW"])

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        return [], [], []

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    with caplog.at_level("WARNING"):
        rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)
    assert rc == 0
    warning = next(
        (r for r in caplog.records if "more cells than the header" in r.getMessage()),
        None,
    )
    assert warning is not None
    assert "https://ragged/1" in warning.getMessage()
    assert "https://fine/1" not in warning.getMessage()


def test_periodic_flush_failure_degrades_then_final_flush_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A periodic-flush OSError sets persistence_degraded + warns (not an
    enrichment failure), enrichment continues, and the final flush retries (F3).
    The data is recovered on disk, but a degraded run exits non-zero so a
    scripted/scheduled caller treats it as not-fully-clean.
    """
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    jobs = [_scraped(f"https://x/{i}", f"Co{i}", comp="$x") for i in range(3)]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda p, **k: '{"fit": 5, "notes": "ok"}'
    )

    # Make the periodic detail-phase flush fail, but let the final flush succeed.
    from daily_driver.plugins.job_search.scraper.csv_io import atomic_write_rows as real

    calls = [0]

    def flaky_atomic(csv_path: Any, header: Any, rows: Any) -> None:
        calls[0] += 1
        if calls[0] == 1:  # first flush (detail phase) fails
            raise OSError("disk hiccup")
        real(csv_path, header, rows)

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.scraper.csv_io.atomic_write_rows",
        flaky_atomic,
    )

    rc = runner.run(
        _enrich_plugin_no_product(), tmp_path, tmp_path, ai=_serial_ctx().ai
    )
    assert rc == 1  # degraded run exits non-zero even though the final flush recovered
    # Final flush retried and succeeded: enrichment is on disk.
    rows = _read_csv(tmp_path / "jobs.csv")
    assert len(rows) == 3
    assert all(r["Fit"] == "5" for r in rows)


def test_interrupt_flush_failure_preserves_exit_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flush OSError on the interrupt path must NOT replace the
    KeyboardInterrupt (no degrade to exit 1) and the manifest is still written
    (F4)."""
    import json

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )
    jobs = [_scraped(f"https://x/{i}", f"Co{i}", comp="$x") for i in range(4)]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", jobs)
        return jobs, [], [("remoteok", jobs)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    calls = [0]

    def fake_invoke(prompt: str, **kw: Any) -> str:
        calls[0] += 1
        if calls[0] >= 2:
            raise KeyboardInterrupt
        return '{"fit": 7, "notes": "first"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    # Every flush raises -- including the wrapper's interrupt-path flush.
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.scraper.csv_io.atomic_write_rows",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")),
    )

    # The KeyboardInterrupt must survive the flush failure on the interrupt path.
    with pytest.raises(KeyboardInterrupt):
        runner.run(_enrich_plugin_no_product(), tmp_path, tmp_path, ai=_serial_ctx().ai)

    # Manifest still written despite the flush failure.
    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["interrupted"] is True


# ── Stage 5: graceful stop during SCRAPING keeps completed work ──────────────


def test_source_checks_stop_event_between_units_and_returns_partial() -> None:
    """A source that yields between its natural units checks ``ctx.stop_event``
    and returns the jobs accumulated so far rather than the rest."""
    import threading

    stop = threading.Event()
    fetched: list[dict[str, Any]] = []

    def fake_source(ctx: ScrapeContext) -> list[dict[str, Any]]:
        # Three units; the event is set after the first, so units 2+ are skipped.
        for i in range(3):
            if ctx.stop_event.is_set():
                return fetched
            fetched.append(_scraped(f"https://u/{i}", f"Co{i}"))
            if i == 0:
                stop.set()  # request stop right after the first unit
        return fetched

    ctx = ScrapeContext(plugin=_us_remote_plugin(), stop_event=stop)
    out = fake_source(ctx)
    # Only the first unit's job is kept; the stop was honored at the next boundary.
    assert [j["company"] for j in out] == ["Co0"]


def test_run_one_marks_interrupted_finish_when_stop_set() -> None:
    """``_run_one`` finishes a stopped-early source with an honest note
    ('interrupted -- N found so far') rather than the normal completion text."""
    import threading

    stop = threading.Event()
    stop.set()
    ctx = ScrapeContext(plugin=_us_remote_plugin(), stop_event=stop)
    done: list[tuple[str, bool, str]] = []

    out = runner._run_one(
        "src",
        ctx,
        on_source_done=lambda sid, ok, detail: done.append((sid, ok, detail)),
        scraper_fn=lambda _ctx: [_scraped("https://a/1", "Acme")],
    )
    assert not isinstance(out, Exception)
    assert done == [("src", True, "interrupted -- 1 found so far")]


def test_orchestrator_drains_partial_on_first_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first Ctrl-C during phase 1 sets the stop event, then DRAINS the still-
    running sources so their partial results are routed through on_source_result
    -- nothing fetched is lost beyond the in-flight unit. The KeyboardInterrupt
    is re-raised so the run is marked interrupted.

    Deterministic: source B signals it is running and then blocks; source A waits
    for B to be running, appends, and its on_source_result callback raises
    KeyboardInterrupt (Ctrl-C landing while B is mid-unit). The drain must set the
    stop event, let B observe it, and still collect B's partial.
    """
    import threading

    b_running = threading.Event()

    def source_a(_ctx: ScrapeContext) -> list[dict[str, Any]]:
        b_running.wait(timeout=5)  # don't finish until B is genuinely running
        return [_scraped("https://a/1", "Acme")]

    def source_b(ctx: ScrapeContext) -> list[dict[str, Any]]:
        b_running.set()
        # B stays in its unit loop until the stop event is observed.
        for _ in range(100):
            if ctx.stop_event.is_set():
                break
            ctx.stop_event.wait(timeout=0.05)
        return [_scraped("https://b/1", "Bravo")]

    monkeypatch.setitem(runner.SCRAPERS, "src_a", source_a)
    monkeypatch.setitem(runner.SCRAPERS, "src_b", source_b)

    collected: list[tuple[str, list[dict[str, Any]]]] = []

    def on_result(sid: str, jobs: list[dict[str, Any]]) -> None:
        collected.append((sid, jobs))
        if sid == "src_a":
            raise KeyboardInterrupt  # Ctrl-C the instant A lands, B still running

    ctx = ScrapeContext(plugin=_us_remote_plugin())

    with pytest.raises(KeyboardInterrupt):
        runner.run_all_scrapers(
            ctx,
            sources_override=["src_a", "src_b"],
            on_source_result=on_result,
        )

    # Both sources' rows were collected -- B's partial was drained, not lost.
    assert {sid for sid, _ in collected} == {"src_a", "src_b"}
    assert ctx.stop_event.is_set()


def test_run_interrupt_during_scrape_keeps_partial_and_marks_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graceful stop during scraping appends what was fetched, dedups it, writes
    the interrupted manifest (phase=scraping), and the partial rows are on disk.

    The orchestrator is stubbed to append source A, then raise KeyboardInterrupt
    (as the real phase-1 drain does once it has routed the partials and re-raised).
    """
    import json

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        # Two sources' partials were drained and appended, then the stop re-raises.
        on_source_result("linkedin", [_scraped("https://l/1", "LinkedInCo")])
        on_source_result("indeed", [_scraped("https://i/1", "IndeedCo")])
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    with pytest.raises(KeyboardInterrupt):
        runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=False)

    rows = _read_csv(tmp_path / "jobs.csv")
    assert sorted(r["Company"] for r in rows) == ["IndeedCo", "LinkedInCo"]

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["interrupted"] is True
    assert manifest["phase_reached"] == "scraping"
    assert manifest["new_jobs"] == 2


def test_cli_run_scrape_returns_130_on_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI maps a scrape-phase KeyboardInterrupt to exit 130 (SIGINT)."""
    import argparse

    from daily_driver.plugins.job_search import cli as jobs_cli

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("linkedin", [_scraped("https://l/1", "LinkedInCo")])
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    class _Workspace:
        output_dir = tmp_path
        ephemeral_dir = tmp_path

        class config:  # noqa: N801
            ai = AIConfig()

            class plugins:  # noqa: N801
                job_search = _us_remote_plugin()

        root = tmp_path

    args = argparse.Namespace(
        dry_run=False,
        no_enrich=True,
        sources=None,
        list_sources=False,
        json=False,
    )
    rc = jobs_cli._run_scrape(args, _Workspace())
    assert rc == 130
    # Partial row landed despite the interrupt.
    assert [r["Company"] for r in _read_csv(tmp_path / "jobs.csv")] == ["LinkedInCo"]


# ── Stage 6: per-unit checkpointing for the slow jobspy sources ──────────────


def test_append_source_repeated_calls_accumulate_funnel_and_dedup(
    tmp_path: Path,
) -> None:
    """Repeated append_source calls for one source_id (the per-unit checkpoint
    path) accumulate the funnel and dedup across units: an intra-source duplicate
    in a later unit is counted 'known', never double-appended."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CANONICAL_HEADER)
    sink = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=CANONICAL_HEADER,
        known_urls=set(),
        known_keys=set(),
        plugin=_us_remote_plugin(),
    )

    # Unit 1: two new rows.
    c1 = sink.append_source(
        "linkedin", [_scraped("https://l/1", "Acme"), _scraped("https://l/2", "Bravo")]
    )
    assert c1["new"] == 2
    # Unit 2: one genuinely new row + one repeat of unit-1's row (dedups to known).
    # The returned counts are the source's CUMULATIVE funnel (setdefault + +=), so
    # after unit 2 they reflect both units: 3 found-new total, 1 known.
    c2 = sink.append_source(
        "linkedin",
        [_scraped("https://l/3", "Charlie"), _scraped("https://l/1", "Acme")],
    )
    assert c2["new"] == 3 and c2["known"] == 1
    # Funnel accumulated across both calls under the one source id.
    funnel = sink.funnel["linkedin"]
    assert funnel == {"found": 4, "new": 3, "known": 1, "loc_skip": 0}
    # No double-append: each unique URL on disk exactly once.
    rows = _read_csv(csv_path)
    assert [r["Company"] for r in rows] == ["Acme", "Bravo", "Charlie"]


def test_run_source_crash_after_checkpointed_units_keeps_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A jobspy-backed source checkpoints two (term x country) units, then crashes
    mid-third-unit (RuntimeError). The two checkpointed units must already be on
    disk -- proving the loss window on a crash/kill two hours in is the in-flight
    unit, not the whole multi-hour source. The crashing source is isolated and
    marked failed (exit 1); the run does not lose the other sources."""
    import json

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    def fake_linkedin(ctx: ScrapeContext, *, sites: Any = None) -> list[dict[str, Any]]:
        # Two finished units handed to the sink, then a crash before the third.
        ctx.checkpoint([_scraped("https://l/u1", "Unit1Co")])
        ctx.checkpoint([_scraped("https://l/u2", "Unit2Co")])
        raise RuntimeError("worker crashed two hours in")

    # The jobspy plan binds runner.scrape_jobspy (not the SCRAPERS registry), so
    # patch the symbol the per-site call closure invokes.
    monkeypatch.setattr(runner, "scrape_jobspy", fake_linkedin)

    # Only linkedin runs; it is a jobspy site so _run_one binds ctx.checkpoint.
    rc = runner.run(
        _us_remote_plugin(),
        tmp_path,
        tmp_path,
        no_enrich=True,
        sources_override=["linkedin"],
    )
    # The crashing source is isolated and marked failed -> exit 1.
    assert rc == 1

    # The two checkpointed units are durable despite the crash before the third.
    rows = _read_csv(tmp_path / "jobs.csv")
    assert sorted(r["Company"] for r in rows) == ["Unit1Co", "Unit2Co"]

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert "linkedin" in manifest["sources_failed"]
    # The checkpointed rows are counted, not lost to the source crash.
    assert manifest["new_jobs"] == 2


def test_checkpointed_source_not_double_appended_at_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that checkpoints per unit and ALSO returns the full list must not
    have its rows appended twice: the orchestrator skips the end-of-source append
    for a checkpointed source."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    rows_out = [_scraped("https://l/1", "Acme"), _scraped("https://l/2", "Bravo")]

    def fake_linkedin(ctx: ScrapeContext, *, sites: Any = None) -> list[dict[str, Any]]:
        for row in rows_out:
            ctx.checkpoint([row])
        return list(rows_out)  # full list also returned (for manifest/results)

    monkeypatch.setattr(runner, "scrape_jobspy", fake_linkedin)

    rc = runner.run(
        _us_remote_plugin(),
        tmp_path,
        tmp_path,
        no_enrich=True,
        sources_override=["linkedin"],
    )
    assert rc == 0
    rows = _read_csv(tmp_path / "jobs.csv")
    # Exactly two rows -- not four. The end-of-source append was skipped.
    assert [r["Company"] for r in rows] == ["Acme", "Bravo"]


def test_first_interrupt_emits_user_note_before_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first Ctrl-C must emit a user-visible note (via on_note) the instant it
    sets the stop event, BEFORE the (possibly minutes-long) drain -- otherwise the
    wait looks like a no-op and the user presses Ctrl-C again and loses the drain.
    """
    import threading

    b_running = threading.Event()

    def source_a(_ctx: ScrapeContext) -> list[dict[str, Any]]:
        b_running.wait(timeout=5)
        return [_scraped("https://a/1", "Acme")]

    def source_b(ctx: ScrapeContext) -> list[dict[str, Any]]:
        b_running.set()
        for _ in range(100):
            if ctx.stop_event.is_set():
                break
            ctx.stop_event.wait(timeout=0.05)
        return [_scraped("https://b/1", "Bravo")]

    monkeypatch.setitem(runner.SCRAPERS, "src_a", source_a)
    monkeypatch.setitem(runner.SCRAPERS, "src_b", source_b)

    notes: list[str] = []

    def on_result(sid: str, jobs: list[dict[str, Any]]) -> None:
        if sid == "src_a":
            raise KeyboardInterrupt  # Ctrl-C while B is still mid-unit

    ctx = ScrapeContext(plugin=_us_remote_plugin())

    with pytest.raises(KeyboardInterrupt):
        runner.run_all_scrapers(
            ctx,
            sources_override=["src_a", "src_b"],
            on_source_result=on_result,
            on_note=notes.append,
        )

    # The interrupt note fired, and it tells the user to press again to abort.
    interrupt_notes = [n for n in notes if "Interrupted" in n]
    assert interrupt_notes, f"no interrupt note among {notes!r}"
    assert "Ctrl-C again" in interrupt_notes[0]


def test_checkpoint_disk_error_stops_source_at_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkpoint persist failure stops the source AT the failing unit: unit 1
    lands, unit 2's append raises ScraperError, the source returns early (no unit
    3 scraped), and the source is marked failed. "failed" means "stopped at the
    failure", not "kept scraping against a dead disk"."""
    import json

    from daily_driver.plugins.job_search.scraper import csv_io
    from daily_driver.plugins.job_search.scraper.runner import (
        CheckpointAborted,
        ScraperError,
    )

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set()),
    )

    real_append = csv_io.append_jobs_typed

    def flaky_append(csv_path: Any, jobs: list[Any], header: Any) -> int:
        # Fail the append of unit 2 (company "Unit2Co"); unit 1 lands normally.
        if jobs and jobs[0].company == "Unit2Co":
            raise ScraperError("disk died mid-scrape")
        return real_append(csv_path, jobs, header)

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.scraper.csv_io.append_jobs_typed",
        flaky_append,
    )

    units_scraped: list[str] = []

    def fake_linkedin(ctx: ScrapeContext, *, sites: Any = None) -> list[dict[str, Any]]:
        # Mirror the real adapter: checkpoint each unit, catch CheckpointAborted
        # to stop at the failing unit and return what is already persisted.
        jobs: list[dict[str, Any]] = []
        for label in ("Unit1Co", "Unit2Co", "Unit3Co"):
            units_scraped.append(label)
            row = _scraped(f"https://l/{label}", label)
            jobs.append(row)
            try:
                ctx.checkpoint([row])
            except CheckpointAborted:
                return jobs
        return jobs

    monkeypatch.setattr(runner, "scrape_jobspy", fake_linkedin)

    rc = runner.run(
        _us_remote_plugin(),
        tmp_path,
        tmp_path,
        no_enrich=True,
        sources_override=["linkedin"],
    )
    # Source stopped at the failure -> marked failed -> exit 1.
    assert rc == 1
    # Unit 3 was never scraped (the source stopped at unit 2's failure).
    assert units_scraped == ["Unit1Co", "Unit2Co"]
    # Unit 1's row is durable; unit 2 (failed append) is not on disk.
    rows = _read_csv(tmp_path / "jobs.csv")
    assert [r["Company"] for r in rows] == ["Unit1Co"]

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert "linkedin" in manifest["sources_failed"]
