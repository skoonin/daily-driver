"""F1: overlapped product + fit/notes enrichment under one shared concurrency cap.

These guard the hard constraint that total concurrent provider calls across BOTH
enrichers never exceed the provider's ``max_parallel`` (they share one bounded
executor, not one pool each), plus interrupt-drain semantics for the overlap.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any

import pytest

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import (
    enrich_product_and_fit_concurrently,
)
from daily_driver.plugins.job_search.scraper.models import EnrichedJob
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched


def _ctx(*, max_parallel: int, budget: int = 50) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "model": "qwen2.5:14b",
                    "max_enrich_companies": budget,
                    "max_enrich_fit": budget,
                    "enrich_gd_rating": False,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig.model_validate({"ollama": {"max_parallel": max_parallel}}),
    )


def _jobs(n: int) -> list[EnrichedJob]:
    # Distinct company per job (so each is its own product lookup AND its own
    # fit/notes job) with a description so notes can be written.
    return [
        make_enriched(
            company=f"Co{i}",
            url=f"https://example.com/{i}",
            product="",
            description_text="We run large-scale infra.",
        )
        for i in range(n)
    ]


def test_total_concurrency_capped_across_both_enrichers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """High-water mark of simultaneous invoke_for calls must stay <= max_parallel
    even though BOTH the product and fit/notes enrichers are submitting work."""
    max_parallel = 3
    n_jobs = 12  # 12 product calls + 12 fit calls = 24 total, well over the cap

    active = [0]
    high_water = [0]
    lock = threading.Lock()

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with lock:
            active[0] += 1
            high_water[0] = max(high_water[0], active[0])
        # Hold the slot briefly so overlap actually occurs.
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        # Product prompts ask "what does X build"; fit prompts ask for JSON.
        if "valid JSON" in prompt:
            return '{"fit": 7, "notes": "ok"}'
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    out, product_stats, fit_stats = enrich_product_and_fit_concurrently(
        _jobs(n_jobs), _ctx(max_parallel=max_parallel)
    )

    assert high_water[0] <= max_parallel, (
        f"high-water concurrency {high_water[0]} exceeded the shared cap "
        f"{max_parallel} — the two enrichers are not sharing one bounded pool"
    )
    # And the overlap must observe real concurrency (otherwise the cap is
    # trivially satisfied by accidental serialization).
    assert high_water[0] >= 2, "expected the overlap to run calls concurrently"
    assert all(j.product == "Some product" for j in out)
    assert all(j.fit == 7 for j in out)
    assert product_stats["enriched"] == n_jobs
    assert fit_stats["enriched"] == n_jobs


def test_serial_provider_runs_both_without_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_parallel=1 must keep every call on the main thread (no overlap pool)."""
    thread_names: list[str] = []

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        thread_names.append(threading.current_thread().name)
        if "valid JSON" in prompt:
            return '{"fit": 7, "notes": "ok"}'
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    out, product_stats, fit_stats = enrich_product_and_fit_concurrently(
        _jobs(3), _ctx(max_parallel=1)
    )

    assert thread_names
    assert all(n == "MainThread" for n in thread_names), thread_names
    assert all(j.product == "Some product" for j in out)
    assert all(j.fit == 7 for j in out)


def test_interrupt_mid_overlap_drains_both_and_reraises_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C mid-overlap: completed product AND fit results both land, and the
    interrupt re-raises exactly once (KeyboardInterrupt)."""
    count = [0]
    count_lock = threading.Lock()
    block = threading.Event()

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with count_lock:
            count[0] += 1
            n = count[0]
        is_fit = "valid JSON" in prompt
        if n <= 4:
            return '{"fit": 7, "notes": "ok"}' if is_fit else "Some product"
        # Later calls block until the test tears down so they're in-flight at
        # interrupt time.
        block.wait(timeout=5.0)
        return '{"fit": 7, "notes": "ok"}' if is_fit else "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    jobs = _jobs(16)

    def trip_sigint() -> None:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with count_lock:
                if count[0] >= 4:
                    break
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=trip_sigint, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        enrich_product_and_fit_concurrently(jobs, _ctx(max_parallel=4))
    block.set()

    products = [j for j in jobs if j.product == "Some product"]
    fits = [j for j in jobs if j.fit == 7]
    # Both enrichers' finished results must have been drained into the jobs list.
    assert products, "expected at least one product stitched after interrupt"
    assert fits, "expected at least one fit applied after interrupt"


def test_stop_event_drains_completed_and_skips_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting ctx.stop_event mid-overlap makes the coordinator drain the already
    completed results, cancel the pending futures, stitch, and return promptly --
    so the wave-1 interrupt join is a real cooperative drain, not a 30s wait that
    lets the daemon spend the full remaining budget.

    The first few calls return fast; later calls block on an event the test never
    releases. Once enough fast results have landed, the test sets stop_event. The
    coordinator must return without those blocked calls completing, applying only
    the results it had in hand.
    """
    max_parallel = 4
    ctx = _ctx(max_parallel=max_parallel)
    count = [0]
    count_lock = threading.Lock()
    block = threading.Event()
    blocking_started = [0]
    # Each blocked call is short; the win is that the coordinator only waits out
    # the calls already in flight at stop (<= max_parallel), not the full budget.
    block_secs = 0.3

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with count_lock:
            count[0] += 1
            n = count[0]
        is_fit = "valid JSON" in prompt
        if n <= max_parallel:
            return '{"fit": 7, "notes": "ok"}' if is_fit else "Some product"
        # Pending calls block. If the cooperative drain works, only the calls
        # already running at stop time keep going -- no NEW ones are started.
        with count_lock:
            blocking_started[0] += 1
        block.wait(timeout=block_secs)
        return '{"fit": 7, "notes": "ok"}' if is_fit else "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    # 16 jobs -> 32 calls; the full remaining budget at block_secs each would be
    # many seconds if the loop ran every future. The drain caps the wait at the
    # in-flight calls (<= max_parallel) plus one block window.
    jobs = _jobs(16)

    def trip_stop() -> None:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with count_lock:
                if count[0] >= max_parallel:
                    break
            time.sleep(0.01)
        ctx.stop_event.set()

    threading.Thread(target=trip_stop, daemon=True).start()
    start = time.time()
    out, product_stats, fit_stats = enrich_product_and_fit_concurrently(jobs, ctx)
    elapsed = time.time() - start
    block.set()

    # Returned after roughly one block window (the in-flight calls), NOT after
    # draining the whole remaining budget serially through the cap.
    assert elapsed < 2.0, f"coordinator did not drain promptly ({elapsed:.2f}s)"
    # No more than the in-flight set ever entered the blocking branch -- pending
    # work was cancelled, not run.
    assert blocking_started[0] <= max_parallel, blocking_started[0]
    # At least the fast results were applied; not every job (we stopped early).
    applied = sum(1 for j in out if j.product == "Some product" or j.fit == 7)
    assert applied >= 1
    assert applied < len(jobs), "expected an early stop, not a full enrichment"


def test_stop_event_unset_leaves_normal_path_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the event never set, the coordinator enriches every job as before."""
    monkeypatch.setattr(
        ai_provider,
        "invoke_for",
        lambda prompt, **k: (
            '{"fit": 7, "notes": "ok"}' if "valid JSON" in prompt else "Some product"
        ),
    )
    ctx = _ctx(max_parallel=4)
    out, product_stats, fit_stats = enrich_product_and_fit_concurrently(_jobs(8), ctx)
    assert all(j.product == "Some product" for j in out)
    assert all(j.fit == 7 for j in out)
    assert product_stats["enriched"] == 8
    assert fit_stats["enriched"] == 8


def test_interrupt_restores_previous_sigint_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the overlapped section the prior SIGINT handler is restored (one
    notifier installed, one restored — not two fighting installs)."""
    monkeypatch.setattr(
        ai_provider,
        "invoke_for",
        lambda prompt, **k: (
            '{"fit": 7, "notes": "ok"}' if "valid JSON" in prompt else "Some product"
        ),
    )
    sentinel = signal.getsignal(signal.SIGINT)
    enrich_product_and_fit_concurrently(_jobs(4), _ctx(max_parallel=4))
    assert signal.getsignal(signal.SIGINT) is sentinel


def test_nan_fit_is_one_counted_failure_others_enrich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NaN fit (parses through json.loads, finite check rejects it) must be a
    single counted failure — not a crash that loses the whole batch. Products
    still stitch and the other jobs still get fits."""

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        if "valid JSON" in prompt:
            # The one bad job returns a non-finite fit; the rest are valid.
            if "Co0 " in prompt or "Co0," in prompt or "at Co0" in prompt:
                return '{"fit": NaN, "notes": "x"}'
            return '{"fit": 7, "notes": "ok"}'
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = _jobs(5)
    out, product_stats, fit_stats = enrich_product_and_fit_concurrently(
        jobs, _ctx(max_parallel=4)
    )

    # Products stitched for everyone (company stitch ran — no crash).
    assert all(j.product == "Some product" for j in out)
    assert product_stats["enriched"] == 5
    # The NaN job is a counted failure; the other 4 enrich.
    assert fit_stats["failed"] >= 1
    assert sum(1 for j in out if j.fit == 7) == 4


def test_worker_exception_is_one_counted_failure_others_enrich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An arbitrary worker exception surfacing at fut.result() must be isolated
    to one counted failure; the rest of the batch still enriches and stitches."""

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        if "valid JSON" in prompt and "Co0" in prompt:
            raise RuntimeError("worker blew up")
        if "valid JSON" in prompt:
            return '{"fit": 7, "notes": "ok"}'
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = _jobs(5)
    out, product_stats, fit_stats = enrich_product_and_fit_concurrently(
        jobs, _ctx(max_parallel=4)
    )

    assert all(j.product == "Some product" for j in out)  # stitch ran
    assert fit_stats["failed"] >= 1
    assert sum(1 for j in out if j.fit == 7) == 4


def test_interrupt_with_failing_drain_still_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If applying a drained future raises during the interrupt drain, the
    KeyboardInterrupt must still propagate (drain errors are swallowed)."""
    count = [0]
    count_lock = threading.Lock()
    block = threading.Event()
    # Set by the 4th provider call: a deterministic signal that the early NaN
    # results exist (so the drain has a failing _apply to swallow) before the
    # interrupt fires -- no polling/deadline race against pool scheduling.
    four_done = threading.Event()

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with count_lock:
            count[0] += 1
            n = count[0]
        is_fit = "valid JSON" in prompt
        if n <= 4:
            if n == 4:
                four_done.set()
            # Some early-finished fit results carry a NaN so the drain's _apply
            # path (which reaches int()) raises mid-drain.
            if is_fit:
                return '{"fit": NaN, "notes": "x"}'
            return "Some product"
        block.wait(timeout=5.0)
        return '{"fit": 7, "notes": "ok"}' if is_fit else "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = _jobs(16)

    def trip_sigint() -> None:
        # Wait on the event rather than polling a wall-clock deadline; only fire
        # once the failing results are guaranteed present.
        if four_done.wait(timeout=10.0):
            os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=trip_sigint, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        enrich_product_and_fit_concurrently(jobs, _ctx(max_parallel=4))
    block.set()


# ── Finding 6: cross-wave exclusion + attempted-identity out-param ───────────


def test_exclude_fit_urls_skips_already_attempted_rows() -> None:
    """A row whose URL is in exclude_fit_urls is not re-enriched (no retry)."""
    jobs = _jobs(4)
    # Pretend the first two were attempted in a prior wave.
    excluded = frozenset({jobs[0].url, jobs[1].url})
    attempted: dict[str, set[str]] = {}

    captured: list[str] = []

    def fake_invoke(prompt: str, **kw: Any) -> str:
        # Tag the response so we can tell which row was processed by company.
        captured.append(prompt)
        return '{"fit": 6, "notes": "ok"}'

    import pytest as _pytest  # local alias to keep top imports unchanged

    with _pytest.MonkeyPatch().context() as mp:
        mp.setattr(ai_provider, "invoke_for", fake_invoke)
        out, _p, fn = enrich_product_and_fit_concurrently(
            jobs,
            _ctx(max_parallel=1),  # serial path
            exclude_fit_urls=excluded,
            attempted=attempted,
        )

    # Only the two non-excluded rows got a fit score.
    assert out[0].fit is None and out[1].fit is None
    assert out[2].fit == 6 and out[3].fit == 6
    # attempted out-param reports exactly the two rows this wave attempted.
    assert attempted["fit_urls"] == {jobs[2].url, jobs[3].url}


def test_attempted_companies_are_unique_not_per_job() -> None:
    """Product attempts are tracked by UNIQUE COMPANY, not per job -- so the
    shared company budget is charged correctly across waves."""
    # Three jobs, two of them at the same company.
    jobs = [
        make_enriched(company="Acme", url="https://x/1", product=""),
        make_enriched(company="Acme", url="https://x/2", product=""),
        make_enriched(company="Bravo", url="https://x/3", product=""),
    ]
    attempted: dict[str, set[str]] = {}

    import pytest as _pytest

    with _pytest.MonkeyPatch().context() as mp:
        mp.setattr(ai_provider, "invoke_for", lambda p, **k: '{"fit": 5, "notes": "n"}')
        enrich_product_and_fit_concurrently(
            jobs, _ctx(max_parallel=1), attempted=attempted
        )

    # Two unique companies attempted (Acme once, Bravo once), not three jobs.
    assert attempted["product_companies"] == {"Acme", "Bravo"}


def test_exclude_companies_skips_already_attempted_company() -> None:
    """A company in exclude_companies is not re-fetched in the later wave."""
    jobs = [
        make_enriched(company="Acme", url="https://x/1", product=""),
        make_enriched(company="Bravo", url="https://x/2", product=""),
    ]
    attempted: dict[str, set[str]] = {}

    import pytest as _pytest

    with _pytest.MonkeyPatch().context() as mp:
        mp.setattr(ai_provider, "invoke_for", lambda p, **k: '{"fit": 5, "notes": "n"}')
        enrich_product_and_fit_concurrently(
            jobs,
            _ctx(max_parallel=1),
            exclude_companies=frozenset({"Acme"}),
            attempted=attempted,
        )

    # Acme excluded -> only Bravo attempted for product.
    assert attempted["product_companies"] == {"Bravo"}
