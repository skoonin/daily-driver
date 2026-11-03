"""``run()`` asks an HTTP provider once before LLM enrichment.

A down or model-less HTTP server (ollama, LM Studio) would otherwise burn one
per-call timeout per job before each LLM phase gives up. The preflight asks the
server ONCE at run start (a single cheap ``GET /models`` on a short, fixed
timeout) and, on failure, skips the LLM passes with one warning while letting
detail-page enrichment (plain HTTP) still run. The preflight runs only when the
run will actually make LLM calls through an HTTP provider; dry-run,
``--no-enrich``, disabled LLM toggles, and a command provider such as claude
must never probe.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from daily_driver.integrations import provider as ai
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from tests.test_plugins.job_search.scraper import claude_provider, ollama_provider


def _scraped_job() -> dict[str, Any]:
    return {
        "url": "https://example.com/job/1",
        "company": "Acme",
        "role": "SRE",
        "source": "remoteok",
        "location": "Remote",
        "comp": "",
        "date_found": "2026-06-10",
    }


def _stub_scrapers(monkeypatch: pytest.MonkeyPatch, jobs: list[dict[str, Any]]) -> None:
    """Make run_all_scrapers return ``jobs`` with no failed sources.

    Drives ``on_source_result`` so the run() sink appends the rows during
    scraping, as the real orchestrator does.
    """
    results = [("remoteok", list(jobs))]

    def fake_run_all(*_a: Any, on_source_result: Any = None, **_kw: Any) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", list(jobs))
        return list(jobs), [], results

    monkeypatch.setattr(runner, "run_all_scrapers", fake_run_all)
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )


def _enrich_plugin() -> JobSearchPlugin:
    """A plugin with the LLM toggles on (the default)."""
    return JobSearchPlugin.model_validate({"scraper": {"enabled": True}})


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _spy_list_models(
    monkeypatch: pytest.MonkeyPatch, returns: list[str]
) -> list[dict[str, Any]]:
    """Record every list_models call (base URL + timeout) and return ``returns``."""
    calls: list[dict[str, Any]] = []

    def fake(provider: Any, *, timeout: int) -> list[str]:
        calls.append({"base_url": provider.settings.base_url, "timeout": timeout})
        return list(returns)

    monkeypatch.setattr(ai, "list_models", fake)
    return calls


def _stub_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(provider: Any, *, timeout: int) -> list[str]:
        # The wording the real list_models raises, so the warning text is real.
        raise ai.AIInvocationError(
            f"{provider.name} not reachable at "
            f"{provider.settings.base_url}/models: connection refused",
            provider=provider.name,
        )

    monkeypatch.setattr(ai, "list_models", unreachable)


def _stub_detail(monkeypatch: pytest.MonkeyPatch, calls: list[int]) -> None:
    """Stub the detail enricher to record invocation and pass jobs through."""

    def fake_detail(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        calls.append(len(jobs))
        return jobs, {
            "enriched": 0,
            "skipped": len(jobs),
            "total": len(jobs),
            "fetched": 0,
        }

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(enrichment_pkg, "enrich_job_details", fake_detail)


def test_preflight_unreachable_skips_llm_but_runs_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Down server: one warning, zero LLM enricher calls, detail still runs, row written."""
    _stub_scrapers(monkeypatch, [_scraped_job()])

    _stub_unreachable(monkeypatch)

    detail_calls: list[int] = []
    _stub_detail(monkeypatch, detail_calls)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def boom_concurrent(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError(
            "LLM enrichment must not run when the server is unreachable"
        )

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", boom_concurrent)

    rc = runner.run(
        _enrich_plugin(),
        tmp_path,
        tmp_path,
        provider=ollama_provider(),
        no_enrich=False,
    )

    assert rc == 0
    # Detail enrichment still runs (plain HTTP, no provider dependency).
    assert detail_calls == [1]
    # Row is still appended.
    rows = _read_csv(tmp_path / "jobs.csv")
    assert len(rows) == 1
    assert rows[0]["Company"] == "Acme"
    # Exactly one warning, naming the provider, endpoint and recovery path.
    err = capsys.readouterr().err
    assert err.lower().count("not reachable") == 1
    assert "ollama" in err
    assert "http://localhost:11434/v1" in err
    assert "ai.providers.ollama.base_url" in err
    assert "backfill" in err.lower()


def test_preflight_unreachable_manifest_zero_enrichment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run manifest records zero LLM enrichment counters on a skipped run."""
    _stub_scrapers(monkeypatch, [_scraped_job()])

    _stub_unreachable(monkeypatch)
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_fit_and_notes",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    runner.run(
        _enrich_plugin(),
        tmp_path,
        tmp_path,
        provider=ollama_provider(),
        no_enrich=False,
    )

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["new_jobs"] == 1
    assert manifest["enriched_fit_notes"] == 0
    assert "enriched_product" not in manifest


def test_preflight_model_missing_skips_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reachable but model not served: one warning names the provider and model."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    _spy_list_models(monkeypatch, returns=["phi4:latest"])  # configured model absent
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_fit_and_notes",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    rc = runner.run(
        _enrich_plugin(),
        tmp_path,
        tmp_path,
        provider=ollama_provider(),
        no_enrich=False,
    )

    assert rc == 0
    err = capsys.readouterr().err
    assert "qwen2.5:14b" in err
    assert "does not list model" in err
    assert err.lower().count("llm enrichment skipped") == 1
    assert "backfill" in err.lower()


def test_preflight_reachable_runs_enrichment_no_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reachable + model present: normal enrichment, no preflight warning."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])
    _stub_detail(monkeypatch, [])

    fit_calls: list[int] = []

    def fake_fit_notes(
        jobs: list[Any],
        ctx: Any,
        *,
        budget: int = 0,
        progress: Any = None,
        flush: Any = None,
        flush_every: int = 25,
        **_kw: Any,
    ) -> Any:
        fit_calls.append(len(jobs))
        return (
            jobs,
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        )

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    rc = runner.run(
        _enrich_plugin(),
        tmp_path,
        tmp_path,
        provider=ollama_provider(),
        no_enrich=False,
    )

    assert rc == 0
    assert fit_calls == [1]
    err = capsys.readouterr().err
    assert "not reachable" not in err.lower()


def test_preflight_uses_short_fixed_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preflight ping uses a short, fixed timeout independent of enrich_timeout."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_fit_and_notes",
        lambda *_a, **_kw: (
            _a[0],
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        ),
    )

    # A long per-call enrich_timeout must NOT bleed into the preflight ping.
    plugin = JobSearchPlugin.model_validate(
        {"scraper": {"enabled": True}, "enrichment": {"enrich_timeout": 120}}
    )

    runner.run(plugin, tmp_path, tmp_path, provider=ollama_provider(), no_enrich=False)

    assert len(calls) == 1
    assert calls[0]["base_url"] == "http://localhost:11434/v1"
    assert calls[0]["timeout"] == runner._HTTP_PREFLIGHT_TIMEOUT
    assert calls[0]["timeout"] <= 5


def test_no_preflight_ping_on_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run performs no preflight ping (no writes, no enrichment at all)."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])

    runner.run(
        _enrich_plugin(), tmp_path, tmp_path, provider=ollama_provider(), dry_run=True
    )

    assert calls == []


def test_no_preflight_ping_on_no_enrich(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--no-enrich performs no preflight ping (every enrichment phase is skipped)."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])

    runner.run(
        _enrich_plugin(), tmp_path, tmp_path, provider=ollama_provider(), no_enrich=True
    )

    assert calls == []


def test_no_preflight_ping_for_command_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command provider is never probed (the enricher's PATH check covers it)."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_fit_and_notes",
        lambda *_a, **_kw: (
            _a[0],
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        ),
    )

    runner.run(
        _enrich_plugin(),
        tmp_path,
        tmp_path,
        provider=claude_provider(),
        no_enrich=False,
    )

    assert calls == []


def test_no_preflight_ping_when_llm_toggles_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All LLM toggles off (detail-only): no ping — detail pages need no provider."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_fit_and_notes",
        lambda *_a, **_kw: (
            _a[0],
            {"enriched": 0, "skipped_budget": 0, "failed": 0},
        ),
    )

    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {"enrich_fit": False, "enrich_notes": False},
        }
    )
    runner.run(plugin, tmp_path, tmp_path, provider=ollama_provider(), no_enrich=False)

    assert calls == []


def test_preflight_command_provider_returns_true_without_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The function itself: a command provider passes with no network call."""
    calls = _spy_list_models(monkeypatch, returns=[])

    assert runner._http_enrichment_preflight(claude_provider()) is True
    assert calls == []


def test_preflight_function_true_only_when_model_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The function itself: True when the model is served, False when it is not."""
    _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])
    assert runner._http_enrichment_preflight(ollama_provider()) is True
    assert runner._http_enrichment_preflight(ollama_provider(model="phi4")) is False
