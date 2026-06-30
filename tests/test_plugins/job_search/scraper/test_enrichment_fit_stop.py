"""Cooperative-stop, interrupt-drain, and cross-wave exclusion for the single
fit/notes pass.

These were ported from the former two-pass coordinator's overlap tests when the
company pass was removed: the cooperative ``ctx.stop_event`` poll, the
interrupt-drain semantics, the shared concurrency cap, SIGINT-handler restore,
the NaN/worker-failure isolation, and the cross-wave ``exclude_urls`` /
``attempted`` out-param now live on ``enrich_fit_and_notes`` directly.
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
from daily_driver.plugins.job_search.scraper.enrichment import enrich_fit_and_notes
from daily_driver.plugins.job_search.scraper.models import EnrichedJob
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched

_FIT_JSON = '{"fit": 7, "notes": "ok"}'


def _ctx(*, max_parallel: int, budget: int = 50) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "enrichment": {
                    "provider": "ollama",
                    "model": "qwen2.5:14b",
                    "max_enrich_fit": budget,
                    "enrich_timeout": 5,
                }
            }
        ),
        ai=AIConfig.model_validate({"ollama": {"max_parallel": max_parallel}}),
    )


def _jobs(n: int) -> list[EnrichedJob]:
    return [
        make_enriched(
            company=f"Co{i}",
            url=f"https://example.com/{i}",
            description_text="We run large-scale infra.",
        )
        for i in range(n)
    ]


def test_total_concurrency_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """High-water mark of simultaneous invoke_for calls must stay <= max_parallel."""
    max_parallel = 3
    n_jobs = 12

    active = [0]
    high_water = [0]
    lock = threading.Lock()

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with lock:
            active[0] += 1
            high_water[0] = max(high_water[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return _FIT_JSON

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)

    out, fit_stats = enrich_fit_and_notes(
        _jobs(n_jobs), _ctx(max_parallel=max_parallel)
    )

    assert (
        high_water[0] <= max_parallel
    ), f"high-water concurrency {high_water[0]} exceeded the cap {max_parallel}"
    assert high_water[0] >= 2, "expected calls to run concurrently"
    assert all(j.fit == 7 for j in out)
    assert fit_stats["enriched"] == n_jobs


def test_serial_provider_runs_on_main_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_parallel=1 must keep every call on the main thread (no pool)."""
    thread_names: list[str] = []

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        thread_names.append(threading.current_thread().name)
        return _FIT_JSON

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    out, _ = enrich_fit_and_notes(_jobs(3), _ctx(max_parallel=1))

    assert thread_names
    assert all(n == "MainThread" for n in thread_names), thread_names
    assert all(j.fit == 7 for j in out)


def test_interrupt_mid_pass_drains_and_reraises_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C mid-pass: completed results land, and the interrupt re-raises
    exactly once (KeyboardInterrupt)."""
    count = [0]
    count_lock = threading.Lock()
    block = threading.Event()

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with count_lock:
            count[0] += 1
            n = count[0]
        if n <= 4:
            return _FIT_JSON
        block.wait(timeout=5.0)
        return _FIT_JSON

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
        enrich_fit_and_notes(jobs, _ctx(max_parallel=4))
    block.set()

    fits = [j for j in jobs if j.fit == 7]
    assert fits, "expected at least one fit applied after interrupt"


def test_stop_event_drains_completed_and_skips_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting ctx.stop_event mid-pass makes the loop drain the already completed
    results, cancel the pending futures, and return promptly -- so the wave-1
    interrupt join is a real cooperative drain, not a wait for the full budget.
    """
    max_parallel = 4
    ctx = _ctx(max_parallel=max_parallel)
    count = [0]
    count_lock = threading.Lock()
    block = threading.Event()
    blocking_started = [0]
    block_secs = 0.3

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with count_lock:
            count[0] += 1
            n = count[0]
        if n <= max_parallel:
            return _FIT_JSON
        with count_lock:
            blocking_started[0] += 1
        block.wait(timeout=block_secs)
        return _FIT_JSON

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
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
    out, fit_stats = enrich_fit_and_notes(jobs, ctx)
    elapsed = time.time() - start
    block.set()

    assert elapsed < 2.0, f"loop did not drain promptly ({elapsed:.2f}s)"
    assert blocking_started[0] <= max_parallel, blocking_started[0]
    applied = sum(1 for j in out if j.fit == 7)
    assert applied >= 1
    assert applied < len(jobs), "expected an early stop, not a full enrichment"


def test_stop_event_unset_leaves_normal_path_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the event never set, the loop enriches every job as before."""
    monkeypatch.setattr(ai_provider, "invoke_for", lambda prompt, **k: _FIT_JSON)
    ctx = _ctx(max_parallel=4)
    out, fit_stats = enrich_fit_and_notes(_jobs(8), ctx)
    assert all(j.fit == 7 for j in out)
    assert fit_stats["enriched"] == 8


def test_stop_event_halts_serial_path_mid_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The serial path (max_parallel=1) must honor ctx.stop_event too: during the
    scrape/enrich overlap it runs on the wave-1 background thread, which never
    receives the KeyboardInterrupt, so stop_event is the only stop signal. Once
    set, no further per-job LLM calls fire; results applied so far are preserved.
    """
    ctx = _ctx(max_parallel=1)
    count = [0]

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        count[0] += 1
        # Trip the stop after the second job's call settles; the loop's top-of-
        # iteration check then breaks before issuing the third call.
        if count[0] == 2:
            ctx.stop_event.set()
        return _FIT_JSON

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    out, _ = enrich_fit_and_notes(_jobs(5), ctx)

    assert (
        count[0] == 2
    ), f"expected the serial loop to stop after 2 calls, got {count[0]}"
    enriched = [j for j in out if j.fit == 7]
    assert len(enriched) == 2  # results applied before the stop are preserved
    assert all(j.fit is None for j in out[2:])  # remaining jobs untouched


def test_interrupt_restores_previous_sigint_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the pass the prior SIGINT handler is restored (one notifier
    installed, one restored)."""
    monkeypatch.setattr(ai_provider, "invoke_for", lambda prompt, **k: _FIT_JSON)
    sentinel = signal.getsignal(signal.SIGINT)
    enrich_fit_and_notes(_jobs(4), _ctx(max_parallel=4))
    assert signal.getsignal(signal.SIGINT) is sentinel


def test_nan_fit_is_one_counted_failure_others_enrich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NaN fit (parses through json.loads, finite check rejects it) must be a
    single counted failure -- not a crash that loses the whole batch."""

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        if "Co0 " in prompt or "Co0," in prompt or "at Co0" in prompt:
            return '{"fit": NaN, "notes": "x"}'
        return _FIT_JSON

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = _jobs(5)
    out, fit_stats = enrich_fit_and_notes(jobs, _ctx(max_parallel=4))

    assert fit_stats["failed"] >= 1
    assert sum(1 for j in out if j.fit == 7) == 4


def test_worker_exception_is_one_counted_failure_others_enrich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An arbitrary worker exception surfacing at fut.result() must be isolated
    to one counted failure; the rest of the batch still enriches."""

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        if "Co0" in prompt:
            raise RuntimeError("worker blew up")
        return _FIT_JSON

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = _jobs(5)
    out, fit_stats = enrich_fit_and_notes(jobs, _ctx(max_parallel=4))

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
    four_done = threading.Event()

    def fake_invoke(prompt: str, **kwargs: Any) -> str:
        with count_lock:
            count[0] += 1
            n = count[0]
        if n <= 4:
            if n == 4:
                four_done.set()
            return '{"fit": NaN, "notes": "x"}'
        block.wait(timeout=5.0)
        return _FIT_JSON

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = _jobs(16)

    def trip_sigint() -> None:
        if four_done.wait(timeout=10.0):
            os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=trip_sigint, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        enrich_fit_and_notes(jobs, _ctx(max_parallel=4))
    block.set()


# ── Cross-wave exclusion + attempted-identity out-param ──────────────────────


def test_exclude_urls_skips_already_attempted_rows() -> None:
    """A row whose URL is in exclude_urls is not re-enriched (no retry)."""
    jobs = _jobs(4)
    excluded = frozenset({jobs[0].url, jobs[1].url})
    attempted: dict[str, set[str]] = {}

    def fake_invoke(prompt: str, **kw: Any) -> str:
        return '{"fit": 6, "notes": "ok"}'

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(ai_provider, "invoke_for", fake_invoke)
        out, _fn = enrich_fit_and_notes(
            jobs,
            _ctx(max_parallel=1),  # serial path
            exclude_urls=excluded,
            attempted=attempted,
        )

    assert out[0].fit is None and out[1].fit is None
    assert out[2].fit == 6 and out[3].fit == 6
    assert attempted["fit_urls"] == {jobs[2].url, jobs[3].url}
