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

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with count_lock:
            count[0] += 1
            n = count[0]
        is_fit = "valid JSON" in prompt
        if n <= 4:
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
