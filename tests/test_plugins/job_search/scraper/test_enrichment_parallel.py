"""Parallelism tests for Ollama-backed enrichment.

These tests cover the thread-pool fan-out introduced for the ollama
provider. The claude path stays serial — see test_claude_path_stays_serial
for the regression guard.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any

import pytest

from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.scraper import enrichment


def _ollama_config(*, max_parallel: int = 4, budget: int = 10) -> dict:
    return {
        "ai": {
            "enrichment": {"provider": "ollama", "model": "qwen2.5:14b"},
            "ollama": {"max_parallel": max_parallel},
        },
        "job_search": {
            "scraper": {
                "max_enrich_companies": budget,
                "max_enrich_fit": budget,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            }
        },
    }


def _claude_config(*, budget: int = 10) -> dict:
    return {
        "ai": {"enrichment": {"provider": "claude"}},
        "job_search": {
            "scraper": {
                "max_enrich_companies": budget,
                "max_enrich_fit": budget,
                "enrich_gd_rating": False,
                "enrich_timeout": 5,
            }
        },
    }


def _job(company: str, idx: int = 0) -> dict[str, Any]:
    return {"company": company, "role": "SRE", "location": "Remote", "product": ""}


def test_ollama_path_runs_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two workers must reach a barrier simultaneously; serial would deadlock."""
    barrier = threading.Barrier(parties=2, timeout=2.0)

    def fake_invoke(task: str, prompt: str, **kwargs: Any) -> str:
        barrier.wait()
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [_job(f"Co{i}") for i in range(4)]
    enrichment.enrich_company_descriptions(jobs, _ollama_config(max_parallel=4))

    assert all(j["product"] == "Some product" for j in jobs)


def test_claude_path_stays_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude provider must never route through the thread pool."""
    thread_names: list[str] = []

    def fake_invoke(task: str, prompt: str, **kwargs: Any) -> str:
        thread_names.append(threading.current_thread().name)
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    monkeypatch.setattr(enrichment.shutil, "which", lambda _name: "/usr/bin/claude")
    jobs = [_job(f"Co{i}") for i in range(4)]
    enrichment.enrich_company_descriptions(jobs, _claude_config())

    assert thread_names, "expected at least one invoke_for call"
    assert all(
        n == "MainThread" for n in thread_names
    ), f"non-main threads used: {thread_names}"


def test_ollama_budget_respected_under_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """budget=3 with 10 jobs must produce exactly 3 invoke_for calls."""
    call_count = 0
    lock = threading.Lock()

    def fake_invoke(task: str, prompt: str, **kwargs: Any) -> str:
        nonlocal call_count
        with lock:
            call_count += 1
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [_job(f"Co{i}") for i in range(10)]
    enrichment.enrich_company_descriptions(jobs, _ollama_config(budget=3))

    assert call_count == 3, f"budget=3 should cap at 3 calls, got {call_count}"


def test_ollama_fit_notes_runs_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """enrich_fit_and_notes must also fan out under ollama provider."""
    barrier = threading.Barrier(parties=2, timeout=2.0)

    def fake_invoke(task: str, prompt: str, **kwargs: Any) -> str:
        barrier.wait()
        return '{"fit": 7, "notes": "ok"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [
        {
            "company": f"Co{i}",
            "role": "SRE",
            "location": "Remote",
            "fit": "",
            "notes": "",
        }
        for i in range(4)
    ]
    enrichment.enrich_fit_and_notes(jobs, _ollama_config())

    assert all(j["fit"] == "7/10" for j in jobs)


def test_worker_log_tag_in_parallel_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When pool_size > 1, worker log lines carry [enrich wN] tag."""
    import logging

    def fake_invoke(task: str, prompt: str, **kwargs: Any) -> str:
        raise ai_provider.AIInvocationError(
            "boom", provider="ollama", returncode=1, stdout="", stderr="boom"
        )

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [_job(f"Co{i}") for i in range(2)]

    with caplog.at_level(logging.WARNING, logger="daily_driver"):
        enrichment.enrich_company_descriptions(jobs, _ollama_config(max_parallel=2))

    tagged = [r for r in caplog.records if "[enrich w" in r.getMessage()]
    assert (
        tagged
    ), f"expected [enrich wN] tagged log, got: {[r.getMessage() for r in caplog.records]}"


def test_ollama_keyboard_interrupt_preserves_partial_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C mid-fan-out: completed worker results must land in jobs."""
    completed_event = threading.Event()
    invocation_count = [0]
    count_lock = threading.Lock()

    def fake_invoke(task: str, prompt: str, **kwargs: Any) -> str:
        # Slow first 3 calls so the main thread can interrupt; fast 4th
        # never gets to run because we SIGINT after 3 finish.
        with count_lock:
            invocation_count[0] += 1
            n = invocation_count[0]
        if n <= 3:
            return f"Product{n}"
        # 4th+ blocks forever (the test is over before this matters).
        completed_event.wait(timeout=5.0)
        return "neverarrives"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [_job(f"Co{i}") for i in range(8)]

    # Schedule SIGINT after the first 3 results arrive.
    def trip_sigint() -> None:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with count_lock:
                if invocation_count[0] >= 3:
                    break
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=trip_sigint, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        enrichment.enrich_company_descriptions(jobs, _ollama_config(max_parallel=4))

    enriched = [j for j in jobs if j["product"].startswith("Product")]
    assert len(enriched) >= 1, f"expected partial results stitched, got: {jobs}"


def test_ollama_interrupt_notifier_uses_user_voice(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """SIGINT message must use user-vocabulary, not engineer-vocabulary."""
    started = threading.Event()
    proceed = threading.Event()

    def fake_invoke(task: str, prompt: str, **kwargs: Any) -> str:
        started.set()
        proceed.wait(timeout=2.0)
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [_job(f"Co{i}") for i in range(4)]

    def trip_sigint() -> None:
        started.wait(timeout=2.0)
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)
        proceed.set()

    threading.Thread(target=trip_sigint, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        enrichment.enrich_company_descriptions(jobs, _ollama_config(max_parallel=4))

    err = capfd.readouterr().err.lower()
    # User-vocabulary signals: an active acknowledgment and the escape hatch.
    # Avoid asserting on absence of specific jargon — copy edits shouldn't
    # break this test as long as the message reads like a user-facing
    # interruption, not a stack trace.
    assert "stopping" in err
    assert "press ctrl-c again" in err


def test_ollama_partial_failure_doesnt_kill_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One worker raising must not abort the others' results."""

    def fake_invoke(task: str, prompt: str, **kwargs: Any) -> str:
        if "BadCo" in prompt:
            raise ai_provider.AIInvocationError(
                "boom", provider="ollama", returncode=1, stdout="", stderr="boom"
            )
        return "Some product"

    monkeypatch.setattr(ai_provider, "invoke_for", fake_invoke)
    jobs = [_job("GoodA"), _job("BadCo"), _job("GoodB"), _job("GoodC")]
    stats = enrichment.enrich_company_descriptions(jobs, _ollama_config())

    enriched = [j for j in jobs if j["product"] == "Some product"]
    assert len(enriched) == 3, f"expected 3 enriched, got {len(enriched)}: {jobs}"
    assert stats["failed"] >= 1
