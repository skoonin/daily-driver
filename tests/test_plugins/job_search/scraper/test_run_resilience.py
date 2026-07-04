"""Run-resilience: per-source append, enrichment flush, overlap, SIGTERM/manifest.

The durable record (jobs.csv) is the checkpoint: each source's rows are appended
as it completes, enrichment updates rows in place with periodic flushes, and a
crash/interrupt loses at most one source or one flush window. These tests inject
failures via stubs (no real signals where avoidable) so the resilience claims are
deterministic.
"""

from __future__ import annotations

import csv
from datetime import date
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
            "locations": {"countries": {"US": []}, "remote": True},
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


def test_append_source_scraped_description_lands_in_sidecar_on_flush(
    tmp_path: Path,
) -> None:
    """A scrape that carries a description (e.g. JobSpy's LinkedIn body) is
    folded into the sidecar store and persisted on the next flush -- so a later
    backfill can hydrate it instead of re-fetching."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
    from daily_driver.plugins.job_search.scraper.descriptions import (
        load_descriptions,
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
    sink.append_source(
        "linkedin",
        [
            _scraped(
                "https://a/1",
                "Acme",
                description_text="Full role description here.",
            )
        ],
    )
    # Not yet on disk until a flush -- append_source only writes jobs.csv rows.
    assert load_descriptions(csv_path) == {}

    sink.flush()

    assert load_descriptions(csv_path) == {
        "https://a/1": "Full role description here.",
    }


def test_flush_persists_description_set_after_construction_by_enrichment(
    tmp_path: Path,
) -> None:
    """detail.py fills description_text by replacing a sink.rows slot with
    ``with_updates`` in place, never through ``append_source``. flush must still
    observe and persist it -- the regression this fix closes."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
    from daily_driver.plugins.job_search.scraper.descriptions import (
        load_descriptions,
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
    # Scraped with no description (the common case for a generic detail-page
    # source): append_source has nothing to fold in yet.
    sink.append_source("greenhouse", [_scraped("https://a/1", "Acme")])
    assert load_descriptions(csv_path) == {}

    # Simulate the detail/linkedin enrichers: replace the row slot in place via
    # with_updates, bypassing append_source entirely.
    sink.rows[0] = sink.rows[0].with_updates(
        description_text="Fetched from the detail page."
    )

    sink.flush()

    assert load_descriptions(csv_path) == {
        "https://a/1": "Fetched from the detail page.",
    }


def test_append_source_strips_url_for_dedup_no_cross_run_duplicate(
    tmp_path: Path,
) -> None:
    """A whitespace-padded scraped URL must dedup against its stripped on-disk
    form. The row is written stripped (RawScrapedJob), and a later run seeds its
    known set from disk (stripped); the in-memory dedup key must match so the
    same padded URL is not re-appended as a duplicate."""
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        load_existing_jobs,
    )

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(CANONICAL_HEADER)

    sink1 = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=CANONICAL_HEADER,
        known_urls=set(),
        known_keys=set(),
        plugin=_us_remote_plugin(),
    )
    padded = "  https://a/1  "
    sink1.append_source("src", [_scraped(padded, "Acme")])
    assert [r["Link"] for r in _read_csv(csv_path)] == ["https://a/1"]

    # Second run: seed known set from disk (stripped) and re-scrape the padded URL.
    known_urls, known_keys, _hdr = load_existing_jobs(csv_path)
    sink2 = runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=CANONICAL_HEADER,
        known_urls=known_urls,
        known_keys=known_keys,
        plugin=_us_remote_plugin(),
    )
    counts = sink2.append_source("src", [_scraped(padded, "AcmeRenamed")])
    assert counts["known"] == 1  # deduped against the stripped on-disk URL
    assert counts["new"] == 0
    assert [r["Link"] for r in _read_csv(csv_path)] == ["https://a/1"]


def test_enriched_from_scraped_coerces_whitespace_only_role() -> None:
    """A whitespace-only role must coerce to '(unknown)', not crash the lift.

    A bare-truthy '   ' skipped the `or '(unknown)'` fallback, then stripped to
    '' and tripped the NonEmptyStr validator -- a ValidationError that escapes
    append_source (which only catches ScraperError) and aborts the run."""
    job = runner._enriched_from_scraped(
        {
            "company": "Acme",
            "role": "   ",
            "url": "https://a/1",
            "source": "remoteok",
            "location": "Remote",
            "comp": "",
            "date_found": "2026-06-10",
        }
    )
    assert job.role == "(unknown)"


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
            "locations": {"countries": {"US": []}, "remote": False},
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
        lambda _csv_path: (set(), set(), {}),
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


def test_run_gcs_orphaned_descriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run drops sidecar entries whose URL is no longer in jobs.csv, keyed on
    the live (pre-scrape) set -- archived rows are excluded from the key."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
        load_descriptions,
    )

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(
            {
                "Status": "found",
                "Company": "Live",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://example.com/live",
                "Source": "remoteok",
                "Date Found": "2026-06-10",
            }
        )
    atomic_write_descriptions(
        csv_path,
        {"https://example.com/live": "keep", "https://example.com/orphan": "drop"},
    )

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        return [], [], []

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)

    assert load_descriptions(csv_path) == {"https://example.com/live": "keep"}


def test_dry_run_leaves_descriptions_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry-run run() must NOT garbage-collect the sidecar (it writes nothing).
    Mirrors the prune-path guard test; without the ``if not dry_run`` gate an
    orphan would be dropped under dry-run."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
        load_descriptions,
    )

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )

    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(
            {
                "Status": "found",
                "Company": "Live",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://example.com/live",
                "Source": "remoteok",
                "Date Found": "2026-06-10",
            }
        )
    store = {"https://example.com/live": "keep", "https://example.com/orphan": "drop"}
    atomic_write_descriptions(csv_path, store)

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        return [], [], []

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    runner.run(_us_remote_plugin(), tmp_path, tmp_path, dry_run=True)

    assert load_descriptions(csv_path) == store


def test_dry_run_appends_nothing_per_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run keeps the in-memory single-pass behavior: no writes at all."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
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
                "max_enrich_fit": budget,
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
    """The fit pass calls the flush hook every ``flush_every`` applied results.

    With 7 fit jobs and flush_every=3, flush fires after results 3 and 6 inside
    the loop, then once on phase completion by the caller -- but the pass itself
    triggers at the 3-result boundaries.
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
    enrichment.enrich_fit_and_notes(
        jobs,
        _serial_ctx(),
        flush=lambda: flush_calls.append(1),
        flush_every=3,
    )
    # 7 fit results -> flush at 3 and 6.
    assert len(flush_calls) >= 2


def test_run_flushes_enrichment_progress_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After enrichment, run() rewrites jobs.csv so Fit/Notes land on disk."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    # comp set -> detail enricher skips the page fetch (no real network);
    # description_text set -> the fit/notes pass reaches the provider.
    jobs = [
        _scraped("https://x/1", "Acme", comp="$200k", description_text="infra"),
        _scraped("https://x/2", "Bravo", comp="$200k", description_text="infra"),
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
    interrupt propagates, so the first row's Fit survives. The fit pass's serial
    path applies each result as the call settles, before the next fetch.
    """
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_fit": 50,
                "enrich_timeout": 5,
            },
        }
    )
    jobs = [
        _scraped(f"https://x/{i}", f"Co{i}", comp="$200k", description_text="infra")
        for i in range(4)
    ]

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
                "max_enrich_fit": budget,
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
) -> list[dict[str, Any]]:
    """Drive run() through a two-wave overlap with a fit-pass stub that records
    each wave's (n, budget, exclusions) and reports the given wave-1 attempted
    fit URLs, mimicking the real fit pass's attempted out-param."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
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

    def fake_fit_notes(
        jobs: list[Any],
        ctx: Any,
        *,
        budget: int = 0,
        progress: Any = None,
        flush: Any = None,
        flush_every: int = 25,
        exclude_urls: frozenset[str] = frozenset(),
        attempted: dict[str, set[str]] | None = None,
        on_planned: Any = None,
        _reset_hint: bool = True,
        force: bool = False,
        cooldown_cutoff: Any = None,
    ) -> Any:
        call[0] += 1
        eligible = [
            j for j in jobs if j.url not in exclude_urls and not (j.fit and j.notes)
        ]
        waves.append(
            {
                "n": len(jobs),
                "fit_budget": budget,
                "exclude_fit_urls": set(exclude_urls),
                "eligible": len(eligible),
            }
        )
        if call[0] == 1 and attempted is not None:
            # Mimic the real fit pass filling the attempted out-param.
            attempted["fit_urls"] = set(wave1_fit_attempted)
        return (
            jobs,
            {
                "enriched": len(eligible),
                "skipped_budget": 0,
                "failed": 0,
            },
        )

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

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
    )
    assert len(waves) == 2
    # Wave 1 enriches the LIVE sink.rows (not a copy) so its slot mutations reach
    # disk; in this synchronous stub the Apple append has already landed, so wave
    # 1 sees all 12 rows. wave1_count still pins the phase-1 boundary for budget.
    # Wave 1 receives the LIVE sink.rows: it always holds the 7 phase-1 rows,
    # and may also see Apple's 5 depending on whether the background wave reads
    # len() before or after the Apple append lands -- a benign race, so pin the
    # invariant (at least phase-1) rather than the timing-dependent exact count.
    assert waves[0]["n"] >= 7
    assert waves[0]["fit_budget"] in (None, 10)  # wave 1 gets the full config budget
    # Wave 2 sees the whole 12-row list but excludes the 7 wave-1 URLs and caps
    # its fit budget at 10 - 7 = 3 (the shared running total).
    assert waves[1]["n"] == 12
    assert waves[1]["fit_budget"] == 3
    assert waves[1]["exclude_fit_urls"] == phase1_urls
    assert waves[1]["eligible"] == 5  # only the Apple rows remain eligible


def test_overlap_wave1_enrichment_reaches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CRITICAL data-loss path: phase-1 rows enriched in the background wave-1
    thread must land in jobs.csv. Drives the REAL coordinator through the overlap
    (on_phase1_done -> background wave), unlike the budget-bookkeeping test that
    stubs the coordinator. comp set -> detail enricher skips network."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    phase1 = [_scraped("https://p1/1", "P1Co", comp="$200k", description_text="infra")]
    apple = [
        _scraped(
            "https://ap/1",
            "ApCo",
            comp="$200k",
            source="apple",
            description_text="infra",
        )
    ]

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
            on_phase1_done(True)  # background wave-1 enrichment starts here
        if on_source_result is not None:
            on_source_result("apple", apple)
        return phase1 + apple, [], [("remoteok", phase1), ("apple", apple)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda prompt, **kw: '{"fit": 9, "notes": "match"}'
    )

    rc = runner.run(
        _overlap_plugin(budget=10),
        tmp_path,
        tmp_path,
        ai=_serial_ctx().ai,
        no_enrich=False,
    )
    assert rc == 0
    rows = {r["Company"]: r for r in _read_csv(tmp_path / "jobs.csv")}
    # Both the phase-1 (wave-1) and Apple (wave-2) rows must carry their Fit.
    assert rows["P1Co"]["Fit"] == "9"
    assert rows["ApCo"]["Fit"] == "9"


def test_overlap_interrupt_joins_wave1_so_its_enrichment_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupt during the Apple (phase-2) scrape must JOIN the background
    wave-1 thread before the final flush, so its in-flight enrichment reaches
    jobs.csv instead of being abandoned. Without the join the phase-1 Fit is lost
    on a launchd SIGTERM / Ctrl-C during overlap."""
    import threading
    import time

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    phase1 = [_scraped("https://p1/1", "P1Co", comp="$200k", description_text="infra")]
    wave1_in_flight = threading.Event()

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
            on_phase1_done(True)  # background wave-1 enrichment starts
        # Apple phase: interrupt once wave 1 is mid-enrichment (still computing).
        wave1_in_flight.wait(timeout=5)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    def fake_invoke(prompt: str, **kw: Any) -> str:
        # Mark in-flight, then keep computing past the main thread's interrupt so
        # the result is NOT yet in sink.rows when run()'s except flush fires. The
        # buggy (no-join) path returns leaving P1Co blank; the join lets it land.
        wave1_in_flight.set()
        time.sleep(0.4)
        return '{"fit": 6, "notes": "match"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    with pytest.raises(KeyboardInterrupt):
        runner.run(
            _overlap_plugin(budget=10),
            tmp_path,
            tmp_path,
            ai=_serial_ctx().ai,
            no_enrich=False,
        )

    rows = {r["Company"]: r for r in _read_csv(tmp_path / "jobs.csv")}
    assert rows["P1Co"]["Fit"] == "6"
    # The interrupted manifest must account the wave-1 enrichment that landed,
    # not undercount it as 0.
    import json

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["interrupted"] is True
    assert manifest["enriched_fit_notes"] >= 1


def test_overlap_interrupt_warns_when_wave1_outlives_join_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When the wave-1 thread does not settle within the (shortened) join bound,
    the handler must DISCLOSE the under-count with a WARNING rather than fail
    silently -- and still write the manifest and exit on the interrupt."""
    import json
    import logging
    import threading

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    # Shorten the bound so the test does not wait the real 30s; the stuck call
    # below outlives it.
    monkeypatch.setattr(runner, "_WAVE1_INTERRUPT_JOIN_SECONDS", 0.3)
    phase1 = [_scraped("https://p1/1", "P1Co", comp="$200k", description_text="infra")]
    wave1_in_flight = threading.Event()
    release = threading.Event()

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
            on_phase1_done(True)
        wave1_in_flight.wait(timeout=5)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    def fake_invoke(prompt: str, **kw: Any) -> str:
        # Block well past the shortened join bound so wave 1 is still alive when
        # the handler gives up. The coordinator's stop_event check cannot cancel a
        # call already in flight, so this models a stuck in-flight LLM call.
        wave1_in_flight.set()
        release.wait(timeout=5)
        return '{"fit": 6, "notes": "match"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(KeyboardInterrupt):
            runner.run(
                _overlap_plugin(budget=10),
                tmp_path,
                tmp_path,
                ai=_serial_ctx().ai,
                no_enrich=False,
            )
    release.set()

    assert any("did not settle within" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]
    # The manifest is still written despite the unsettled wave.
    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["interrupted"] is True


def test_overlap_interrupt_logs_relayed_wave1_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A wave-1 exception must be surfaced (logged) on the interrupt path, not
    vanish -- and the interrupt exit code still wins (the error is not re-raised
    over it)."""
    import logging
    import threading

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    phase1 = [_scraped("https://p1/1", "P1Co", comp="$200k")]
    wave1_failed = threading.Event()

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
            on_phase1_done(True)
        # Wait for wave 1 to fail, then interrupt the main thread.
        wave1_failed.wait(timeout=5)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    # A wave-level failure (not a per-result one, which _guarded_consume swallows)
    # escapes the fit pass and is relayed via _run_wave1's except into
    # wave1_error. Patch the fit pass to raise so the relay path is exercised.
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def boom_fit_notes(*_a: Any, **_kw: Any) -> Any:
        wave1_failed.set()
        raise RuntimeError("wave-1 boom")

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", boom_fit_notes)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(KeyboardInterrupt):
            runner.run(
                _overlap_plugin(budget=10),
                tmp_path,
                tmp_path,
                ai=_serial_ctx().ai,
                no_enrich=False,
            )

    assert any(
        "wave-1 enrichment raised before the interrupt" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_disabled_passes_render_no_bars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pass disabled by config gets NO phase row at all -- a pinned bar with
    a placeholder total for work that never runs reads as a stuck toggle. With
    fit/notes off, only the Detail pages bar renders."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
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
        ai_provider,
        "invoke_for",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no LLM call expected with fit/notes off")
        ),
    )
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "enrich_fit": False,
                "enrich_notes": False,
                "enrich_timeout": 5,
            },
        }
    )
    rc = runner.run(plugin, tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False)
    assert rc == 0
    err = capsys.readouterr().err
    assert "Detail pages" in err
    assert "Fit and notes" not in err


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
    )
    assert len(waves) == 2
    assert waves[1]["fit_budget"] == 0


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
    )
    # Wave 2 excludes all 4 attempted rows (incl. the failed one) -> 3 eligible.
    assert waves[1]["exclude_fit_urls"] == phase1_urls
    assert waves[1]["eligible"] == 3
    # Budget reduced by the 4 attempted (not re-charged): 10 - 4 = 6.
    assert waves[1]["fit_budget"] == 6


# ── Stage 4: SIGTERM, manifest fields, status recovery line ──────────────────


def test_manifest_records_phase_reached_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean run records phase_reached=complete and interrupted=false."""
    import json

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
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
        lambda _csv_path: (set(), set(), {}),
    )
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_fit": 50,
                "enrich_timeout": 5,
            },
        }
    )
    jobs = [
        _scraped(f"https://x/{i}", f"Co{i}", comp="$x", description_text="infra")
        for i in range(4)
    ]

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


def _enrich_plugin_serial() -> JobSearchPlugin:
    # The serial fit pass applies results incrementally (one per call).
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "claude",
                "max_enrich_fit": 50,
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
        lambda _csv_path: (set(), set(), {}),
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
        lambda _csv_path: (set(), set(), {}),
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
        lambda _csv_path: (set(), set(), {}),
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


def test_csv_row_identity_tolerates_short_rows() -> None:
    """A jobs.csv row shorter than the header (a hand-edit slip) yields None cell
    values from csv.DictReader; _csv_row_identity must not crash on them (the H1
    flush re-reads on-disk rows and would otherwise raise AttributeError mid-run).
    """
    # Link present -> URL identity, even with None Company/Role.
    assert (
        runner._csv_row_identity(
            {"Link": "https://x/1", "Company": None, "Role": None}  # type: ignore[dict-item]
        )
        == "https://x/1"
    )
    # No Link, None Company/Role (row truncated before those cells) -> no crash.
    ident = runner._csv_row_identity(
        {"Link": None, "Company": None, "Role": None}  # type: ignore[dict-item]
    )
    assert isinstance(ident, str)


def test_flush_does_not_clobber_concurrent_prune_and_edit(tmp_path: Path) -> None:
    """A run-path flush must re-read disk and merge by identity, not replay the
    stale snapshot captured at run start (audit H1).

    The sentinel lock is released for the long scrape/enrich phase, so a
    concurrent ``prune`` or hand-edit can change jobs.csv while the run holds an
    in-memory snapshot. The next flush must NOT resurrect a pruned row or revert
    a field edit the run did not make.
    """
    from daily_driver.plugins.job_search.scraper.csv_io import (
        CANONICAL_HEADER,
        load_existing_jobs,
        read_rows,
    )

    csv_path = tmp_path / "jobs.csv"
    seed_rows = [
        {
            "Status": "new",
            "Company": "PruneCo",
            "Role": "SRE",
            "Link": "https://old/prune",
        },
        {
            "Status": "new",
            "Company": "EditCo",
            "Role": "SRE",
            "Link": "https://old/edit",
        },
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CANONICAL_HEADER)
        w.writeheader()
        w.writerows(seed_rows)

    # Mirror run()'s setup: snapshot dedup state + pre-existing rows under the
    # initial lock, then build the sink (the run path releases the lock here).
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
    # This run scrapes one new row, appended to disk immediately.
    sink.append_source("remoteok", [_scraped("https://new/1", "NewCo")])

    # Concurrent writer (prune + status edit) takes the released lock mid-run:
    # drop the pruned row and advance the edited row's status, keeping the run's
    # already-appended row.
    _, on_disk = read_rows(csv_path)
    survivors = []
    for row in on_disk:
        if (row.get("Link") or "").strip() == "https://old/prune":
            continue
        if (row.get("Link") or "").strip() == "https://old/edit":
            row = {**row, "Status": "applied"}
        survivors.append(row)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=file_header)
        w.writeheader()
        w.writerows(survivors)

    # The run's next flush must honor the concurrent changes, not overwrite them.
    sink.flush()

    rows = _read_csv(csv_path)
    links = [r["Link"] for r in rows]
    assert (
        "https://old/prune" not in links
    ), "flush resurrected a concurrently pruned row"
    assert links == ["https://old/edit", "https://new/1"]
    edited = next(r for r in rows if r["Link"] == "https://old/edit")
    assert edited["Status"] == "applied", "flush reverted a concurrent status edit"


def test_run_enrichment_flush_preserves_preexisting_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An enriching run against a populated jobs.csv must keep every
    pre-existing row through the enrichment flushes (regression: the flush
    rewrote the file from only this run's rows, wiping the rest)."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
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

    jobs = [_scraped("https://x/1", "Acme", comp="$200k", description_text="infra")]

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
        lambda _csv_path: (set(), set(), {}),
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
        lambda _csv_path: (set(), set(), {}),
    )
    jobs = [
        _scraped(f"https://x/{i}", f"Co{i}", comp="$x", description_text="infra")
        for i in range(3)
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

    rc = runner.run(_enrich_plugin_serial(), tmp_path, tmp_path, ai=_serial_ctx().ai)
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
        lambda _csv_path: (set(), set(), {}),
    )
    jobs = [
        _scraped(f"https://x/{i}", f"Co{i}", comp="$x", description_text="infra")
        for i in range(4)
    ]

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
        runner.run(_enrich_plugin_serial(), tmp_path, tmp_path, ai=_serial_ctx().ai)

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


def test_run_one_records_degraded_and_keeps_partial_jobs() -> None:
    """A source raising PartialSourceError is DEGRADED, not failed: ``_run_one``
    returns the partial jobs carried on the exception (so they still append),
    fires on_source_degraded with the reason, and finishes the row ok=True with a
    'degraded' note rather than a clean completion or a failure."""
    ctx = ScrapeContext(plugin=_us_remote_plugin())
    done: list[tuple[str, bool, str]] = []
    degraded: list[tuple[str, str]] = []

    def scraper_fn(_ctx: ScrapeContext) -> list[dict[str, Any]]:
        raise runner.PartialSourceError(
            [_scraped("https://a/1", "Acme")], "1 of 2 boards failed: down"
        )

    out = runner._run_one(
        "ashby",
        ctx,
        on_source_done=lambda sid, ok, detail: done.append((sid, ok, detail)),
        scraper_fn=scraper_fn,
        on_source_degraded=lambda sid, reason: degraded.append((sid, reason)),
    )

    # The partial jobs are returned (NOT the exception), so they still append.
    assert not isinstance(out, Exception)
    assert [j["company"] for j in out] == ["Acme"]
    assert degraded == [("ashby", "1 of 2 boards failed: down")]
    sid, ok, detail = done[0]
    assert sid == "ashby"
    assert ok is True  # degraded is distinct from failed (ok=False)
    assert "degraded -- 1 found" in detail


def test_run_records_degraded_sources_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded source (incomplete scrape) lands in the manifest's
    ``sources_degraded`` -- distinct from ``sources_failed`` -- while its kept
    rows still persist and the run is not treated as a hard failure."""
    import json

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )

    def fake_scrape(
        ctx: Any,
        *_a: Any,
        on_source_result: Any = None,
        on_source_degraded: Any = None,
        **_kw: Any,
    ) -> Any:
        job = _scraped("https://w/1", "Acme", source="Workday (acme)")
        # The source completed but its scrape was incomplete -> degraded, kept.
        on_source_degraded("workday", "1 of 1 boards returned incomplete results")
        on_source_result("workday", [job])
        return ([job], [], [("workday", [job])])

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)

    # Degraded is not a hard failure: exit stays 0 and the row persists.
    assert rc == 0
    rows = _read_csv(tmp_path / "jobs.csv")
    assert [r["Company"] for r in rows] == ["Acme"]

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["sources_degraded"] == ["workday"]
    assert manifest["sources_failed"] == []
    # A degraded source is excluded from sources_ok (its scrape is incomplete).
    assert "workday" not in manifest["sources_ok"]


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
        lambda _csv_path: (set(), set(), {}),
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
        lambda _csv_path: (set(), set(), {}),
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
        lambda _csv_path: (set(), set(), {}),
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
        lambda _csv_path: (set(), set(), {}),
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
        lambda _csv_path: (set(), set(), {}),
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


def test_run_completion_line_reports_total_run_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The end-of-run summary names the total wall-clock duration."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", [_scraped("https://a/1", "Acme")])
        return [], [], []

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)
    assert rc == 0
    err = capsys.readouterr().err
    # Collapse whitespace: the completion line embeds the (long) csv path, so on
    # a narrow console the Rich-wrapped output can split "Total run time:".
    assert "Total run time:" in " ".join(err.split())


# ── Upsert-on-rescan: heal descriptions + refresh Date Verified ─────────────


def _seed_jobs_csv(csv_path: Path, rows: list[dict[str, str]]) -> None:
    """Write a jobs.csv with the canonical header and the given rows."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _rescan_sink(
    csv_path: Path,
    tmp_path: Path,
    *,
    descriptions: dict[str, str] | None = None,
) -> runner._JobSink:
    """Build a run-path sink seeded from jobs.csv, mirroring run()'s setup."""
    from daily_driver.plugins.job_search.scraper.csv_io import (
        load_existing_jobs,
        read_rows,
    )

    known_urls, known_keys, file_header = load_existing_jobs(csv_path)
    _, preexisting = read_rows(csv_path)
    return runner._JobSink(
        csv_path=csv_path,
        lock_path=tmp_path / ".lock",
        header=file_header,
        known_urls=known_urls,
        known_keys=known_keys,
        plugin=_us_remote_plugin(),
        preexisting_rows=preexisting,
        descriptions=descriptions,
    )


def test_reseen_known_url_bumps_date_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-seeing a known URL bumps its Date Verified to today, with no new or
    duplicate row -- the freshness signal that makes a last-seen prune reliable."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            {
                "Status": "found",
                "Company": "Acme",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://a/1",
                "Source": "remoteok",
                "Date Found": "2026-06-01",
                "Date Verified": "2026-06-01",
            }
        ],
    )
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    counts = sink.append_source("remoteok", [_scraped("https://a/1", "Acme")])
    assert counts["known"] == 1
    assert counts["new"] == 0

    sink.flush()

    rows = _read_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["Date Verified"] == "2026-07-03"
    assert rows[0]["Date Found"] == "2026-06-01"
    assert sink.rescan_summary() == (1, 0, 0)  # (still_visible, not_seen, healed)


def test_unseen_row_keeps_old_date_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing row NOT returned by the scrape keeps its old Date Verified
    even when other URLs are re-seen -- this is what makes deleting by last-seen
    safe (a live-but-unscraped row is not silently aged forward)."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            {
                "Status": "found",
                "Company": "Seen",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://a/1",
                "Source": "remoteok",
                "Date Found": "2026-06-01",
                "Date Verified": "2026-06-01",
            },
            {
                "Status": "found",
                "Company": "Unseen",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://a/2",
                "Source": "remoteok",
                "Date Found": "2026-06-01",
                "Date Verified": "2026-06-01",
            },
        ],
    )
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    # Only a/1 is re-scraped; a/2 is absent from this run's scrape.
    sink.append_source("remoteok", [_scraped("https://a/1", "Seen")])
    sink.flush()

    rows = {r["Company"]: r for r in _read_csv(csv_path)}
    assert rows["Seen"]["Date Verified"] == "2026-07-03"
    assert rows["Unseen"]["Date Verified"] == "2026-06-01"
    # One re-confirmed live, one carried but not seen this run.
    assert sink.rescan_summary() == (1, 1, 0)  # (still_visible, not_seen, healed)


def test_reseen_known_url_heals_missing_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-scrape that carries a description heals a known row that had none in
    the sidecar (the Indeed-via-JobSpy case), so the next backfill can score it.
    The row is not duplicated."""
    from daily_driver.plugins.job_search.scraper.descriptions import (
        load_descriptions,
    )

    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            {
                "Status": "found",
                "Company": "Acme",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://a/1",
                "Source": "indeed",
                "Date Found": "2026-06-01",
                "Date Verified": "2026-06-01",
            }
        ],
    )
    assert load_descriptions(csv_path) == {}
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    sink.append_source(
        "indeed",
        [_scraped("https://a/1", "Acme", description_text="Full role body.")],
    )
    sink.flush()

    assert load_descriptions(csv_path) == {"https://a/1": "Full role body."}
    assert len(_read_csv(csv_path)) == 1
    assert sink.rescan_summary() == (1, 0, 1)  # (still_visible, not_seen, healed)


def test_reseen_emits_verbose_log_lines_on_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At verbose (INFO) level, each re-seen row's Date Verified refresh and each
    healed description is logged per-row, so ``-v`` shows exactly what changed."""
    import logging

    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            {
                "Status": "found",
                "Company": "Acme",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://a/1",
                "Source": "indeed",
                "Date Found": "2026-06-01",
                "Date Verified": "2026-06-01",
            }
        ],
    )
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    sink.append_source(
        "indeed",
        [_scraped("https://a/1", "Acme", description_text="Full role body.")],
    )
    with caplog.at_level(logging.INFO, logger="daily_driver"):
        sink.flush()

    messages = [r.getMessage() for r in caplog.records]
    assert any("Date Verified -> 2026-07-03" in m for m in messages), messages
    assert any("Healed missing description" in m for m in messages), messages


def test_reseen_does_not_overwrite_existing_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Healing is fill-only: when the sidecar already holds a description, a
    re-scrape's (possibly truncated) body must NOT overwrite it."""
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
        load_descriptions,
    )

    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            {
                "Status": "found",
                "Company": "Acme",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://a/1",
                "Source": "indeed",
                "Date Found": "2026-06-01",
                "Date Verified": "2026-06-01",
            }
        ],
    )
    atomic_write_descriptions(csv_path, {"https://a/1": "Original full description."})
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path, descriptions=load_descriptions(csv_path))
    sink.append_source(
        "indeed",
        [_scraped("https://a/1", "Acme", description_text="Trunc")],
    )
    sink.flush()

    assert load_descriptions(csv_path) == {"https://a/1": "Original full description."}


def test_run_reports_reseen_summary_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A full run prints the re-sighting freshness split: one pre-existing row is
    re-scraped (still visible), one is not returned (not seen this run)."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_HEADER, extrasaction="ignore")
        writer.writeheader()
        for company, link in (("SeenCo", "https://x/1"), ("GoneCo", "https://x/2")):
            writer.writerow(
                {
                    "Status": "found",
                    "Company": company,
                    "Role": "SRE",
                    "Location": "Remote",
                    "Link": link,
                    "Source": "remoteok",
                    "Date Found": "2026-06-01",
                    "Date Verified": "2026-06-01",
                }
            )

    # Re-scrape only x/1; x/2 is absent this run. comp set -> detail enricher
    # skips the network fetch.
    rescan = [_scraped("https://x/1", "SeenCo", comp="$200k", description_text="infra")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", rescan)
        return rescan, [], [("remoteok", rescan)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)
    from daily_driver.integrations import ai_provider

    monkeypatch.setattr(
        ai_provider, "invoke_for", lambda prompt, **kw: '{"fit": 7, "notes": "ok"}'
    )

    rc = runner.run(
        _enrich_plugin(), tmp_path, tmp_path, ai=_serial_ctx().ai, no_enrich=False
    )
    assert rc == 0
    err = " ".join(capsys.readouterr().err.split())
    # Two labeled funnels; the re-sighting split sits under Scraping.
    assert "Scraping" in err
    assert "Enrichment" in err
    assert "1 still visible, 1 not seen this run" in err


def test_run_no_enrich_persists_resightings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-sighting is a scrape fact, so a --no-enrich run still refreshes
    Date Verified and heals a missing description (the one full-file rewrite it
    makes) and reports the Scraping funnel -- but no Enrichment section."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
    from daily_driver.plugins.job_search.scraper.descriptions import (
        load_descriptions,
    )

    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )
    csv_path = tmp_path / "jobs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(
            {
                "Status": "found",
                "Company": "Acme",
                "Role": "SRE",
                "Location": "Remote",
                "Link": "https://x/1",
                "Source": "indeed",
                "Date Found": "2026-06-01",
                "Date Verified": "2026-06-01",
            }
        )
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    rescan = [
        _scraped("https://x/1", "Acme", description_text="Full body from scrape.")
    ]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", rescan)
        return rescan, [], [("remoteok", rescan)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)
    assert rc == 0

    rows = _read_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["Date Verified"] == "2026-07-03"
    assert load_descriptions(csv_path) == {"https://x/1": "Full body from scrape."}
    err = " ".join(capsys.readouterr().err.split())
    assert "Scraping" in err
    assert "1 still visible, 0 not seen this run" in err
    # No enrichment ran, so no Enrichment section.
    assert "Enrichment" not in err


# ── Source-upgrade preference: board record beats aggregator row ─────────────


def _linkedin_row(**overrides: str) -> dict[str, str]:
    row = {
        "Status": "found",
        "Company": "Acme",
        "Role": "SRE",
        "Location": "Remote",
        "Link": "https://linkedin.com/jobs/view/1",
        "Source": "linkedin",
        "Date Found": "2026-06-01",
        "Date Verified": "2026-06-01",
    }
    row.update(overrides)
    return row


def _board_twin(**extra: Any) -> dict[str, Any]:
    """The same Acme SRE job as its greenhouse board record (different URL)."""
    return _scraped(
        "https://boards.greenhouse.io/acme/jobs/9",
        "Acme",
        source="Greenhouse (acme)",
        **extra,
    )


def test_cross_run_board_twin_upgrades_stored_aggregator_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored LinkedIn row whose company+role twin arrives from a board source
    gets its Source/Link replaced by the board record -- the URL that board-diff
    closure can verify -- while triage-owned cells stay untouched."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [_linkedin_row()])
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    counts = sink.append_source("greenhouse", [_board_twin()])
    assert counts["known"] == 1  # key collision, not appended
    sink.flush()

    rows = _read_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["Source"] == "Greenhouse (acme)"
    assert rows[0]["Link"] == "https://boards.greenhouse.io/acme/jobs/9"
    assert rows[0]["Status"] == "found"
    assert rows[0]["Date Found"] == "2026-06-01"
    assert rows[0]["Date Verified"] == "2026-07-03"


def test_same_run_aggregator_then_board_ends_upgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Append-as-completed lands the fast aggregator row first; the board twin
    arriving later the same run still wins by flush time."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [])
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    first = sink.append_source(
        "linkedin",
        [_scraped("https://linkedin.com/jobs/view/1", "Acme", source="linkedin")],
    )
    assert first["new"] == 1
    second = sink.append_source(
        "greenhouse", [_board_twin(description_text="Board body.")]
    )
    assert second["known"] == 1
    sink.flush()

    rows = _read_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["Source"] == "Greenhouse (acme)"
    assert rows[0]["Link"] == "https://boards.greenhouse.io/acme/jobs/9"
    # The fill-only description landed on the in-run row (model field), so the
    # sidecar fold files it under the board URL.
    assert sink.rows[0].description_text == "Board body."
    assert sink.upgraded == 1


def test_aggregator_twin_never_downgrades_board_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            _linkedin_row(
                Link="https://boards.greenhouse.io/acme/jobs/9",
                Source="Greenhouse (acme)",
            )
        ],
    )
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    sink.append_source(
        "linkedin",
        [_scraped("https://linkedin.com/jobs/view/1", "Acme", source="linkedin")],
    )
    sink.flush()

    rows = _read_csv(csv_path)
    assert rows[0]["Source"] == "Greenhouse (acme)"
    assert rows[0]["Link"] == "https://boards.greenhouse.io/acme/jobs/9"
    assert rows[0]["Date Verified"] == "2026-07-03"  # still a re-sighting


def test_board_to_board_twin_keeps_first_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No preference between two board sources: first record stays."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(
        csv_path,
        [
            _linkedin_row(
                Link="https://boards.greenhouse.io/acme/jobs/9",
                Source="Greenhouse (acme)",
            )
        ],
    )
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    sink.append_source(
        "lever",
        [_scraped("https://jobs.lever.co/acme/9", "Acme", source="Lever (acme)")],
    )
    sink.flush()

    rows = _read_csv(csv_path)
    assert rows[0]["Source"] == "Greenhouse (acme)"
    assert rows[0]["Link"] == "https://boards.greenhouse.io/acme/jobs/9"


def test_triaged_row_bumps_date_verified_but_never_upgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record behind an application never changes under the user -- but the
    cross-source re-sighting still counts as liveness evidence (the Date
    Verified bump cross-source key collisions previously missed entirely)."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [_linkedin_row(Status="applied")])
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    sink.append_source("greenhouse", [_board_twin()])
    sink.flush()

    rows = _read_csv(csv_path)
    assert rows[0]["Source"] == "linkedin"
    assert rows[0]["Link"] == "https://linkedin.com/jobs/view/1"
    assert rows[0]["Date Verified"] == "2026-07-03"
    assert sink.rescan_summary() == (1, 0, 0)


def test_upgrade_fills_blank_comp_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [_linkedin_row()])
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    sink.append_source("greenhouse", [_board_twin(comp="$100,000–200,000/yr USD")])
    sink.flush()
    assert _read_csv(csv_path)[0]["Comp"] == "$100,000–200,000/yr USD"

    # A hand-set or already-filled Comp is never overwritten.
    _seed_jobs_csv(csv_path, [_linkedin_row(Comp="$1/yr")])
    sink2 = _rescan_sink(csv_path, tmp_path)
    sink2.append_source("greenhouse", [_board_twin(comp="$100,000–200,000/yr USD")])
    sink2.flush()
    assert _read_csv(csv_path)[0]["Comp"] == "$1/yr"


def test_upgrade_heals_description_under_new_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The heal keys off the row's post-upgrade Link, so the board description
    lands under the board URL, not the dead aggregator one."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [_linkedin_row()])
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    # Seeded non-empty: the sink treats a falsy descriptions dict as absent.
    descriptions: dict[str, str] = {"https://other/1": "unrelated"}
    sink = _rescan_sink(csv_path, tmp_path, descriptions=descriptions)
    sink.append_source("greenhouse", [_board_twin(description_text="Board body.")])
    sink.flush()

    assert _read_csv(csv_path)[0]["Link"] == "https://boards.greenhouse.io/acme/jobs/9"
    assert descriptions.get("https://boards.greenhouse.io/acme/jobs/9") == "Board body."


def test_board_sighting_wins_reseen_slot_over_aggregator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the SAME key is re-seen by an aggregator and a board source in one
    run, the board sighting owns the collector slot regardless of order, so the
    upgrade still fires."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [_linkedin_row()])
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    # Aggregator re-sighting first (with a description -- the old tiebreak).
    sink.append_source(
        "linkedin",
        [
            _scraped(
                "https://linkedin.com/jobs/view/1",
                "Acme",
                source="linkedin",
                description_text="Aggregator body.",
            )
        ],
    )
    sink.append_source("greenhouse", [_board_twin()])
    sink.flush()

    rows = _read_csv(csv_path)
    assert rows[0]["Source"] == "Greenhouse (acme)"


def test_periodic_flush_before_board_twin_leaves_no_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A periodic flush lands the aggregator append on disk mid-run; the board
    twin then arrives and the upgrade rewrites the row. The pre-upgrade on-disk
    append must be excluded by its OLD identity on every later flush -- one row,
    upgraded, across repeated flushes."""
    csv_path = tmp_path / "jobs.csv"
    _seed_jobs_csv(csv_path, [])
    monkeypatch.setattr(runner, "today", lambda: date(2026, 7, 3))

    sink = _rescan_sink(csv_path, tmp_path)
    sink.append_source(
        "linkedin",
        [_scraped("https://linkedin.com/jobs/view/1", "Acme", source="linkedin")],
    )
    sink.flush()  # periodic: pre-upgrade row now on disk
    sink.append_source("greenhouse", [_board_twin()])
    sink.flush()  # upgrade fires here
    sink.flush()  # and must stay idempotent

    rows = _read_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["Source"] == "Greenhouse (acme)"
    assert rows[0]["Link"] == "https://boards.greenhouse.io/acme/jobs/9"
    assert sink.upgraded == 1
