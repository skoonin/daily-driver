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
    assert waves[0]["fit_budget"] in (0, 10)  # wave 1 gets the full config budget
    # Wave 2 sees the whole 12-row list but excludes the 7 wave-1 URLs and caps
    # its fit budget at 10 - 7 = 3 (the shared running total).
    assert waves[1]["n"] == 12
    assert waves[1]["fit_budget"] == 3
    assert waves[1]["exclude_fit_urls"] == phase1_urls
    assert waves[1]["eligible"] == 5  # only the Apple rows remain eligible


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
    assert rc == 0  # degraded, not failed
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
