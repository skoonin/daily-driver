"""``run()`` pings ollama once before LLM enrichment when routed there.

A down or model-less ollama server would otherwise burn one per-call timeout
per job before each LLM phase gives up. The preflight pings the server ONCE at
run start (a single cheap ``list_models`` GET on a short, fixed timeout) and,
on failure, skips the LLM passes with one warning while letting detail-page
enrichment (plain HTTP) still run. The preflight runs only when the run will
actually make ollama LLM calls; dry-run, ``--no-enrich``, and the claude
provider must never ping.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ollama_client
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner


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
        lambda _csv_path: (set(), set()),
    )


def _ollama_plugin() -> JobSearchPlugin:
    """A plugin configured to enrich via ollama with the LLM toggles on."""
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {"provider": "ollama", "model": "qwen2.5:14b"},
        }
    )


def _ollama_ai() -> AIConfig:
    return AIConfig.model_validate({"ollama": {"endpoint": "http://localhost:11434"}})


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _spy_list_models(
    monkeypatch: pytest.MonkeyPatch, returns: list[str]
) -> list[dict[str, Any]]:
    """Record every list_models call (endpoint + timeout) and return ``returns``."""
    calls: list[dict[str, Any]] = []

    def fake(endpoint: str, timeout: int = 5) -> list[str]:
        calls.append({"endpoint": endpoint, "timeout": timeout})
        return list(returns)

    monkeypatch.setattr(ollama_client, "list_models", fake)
    return calls


def _stub_detail(monkeypatch: pytest.MonkeyPatch, calls: list[int]) -> None:
    """Stub the detail enricher to record invocation and pass jobs through."""

    def fake_detail(jobs: list[Any], ctx: Any, *, progress: Any = None) -> Any:
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

    def unreachable(endpoint: str, timeout: int = 5) -> list[str]:
        raise ollama_client.OllamaNotReachableError(f"down at {endpoint}")

    monkeypatch.setattr(ollama_client, "list_models", unreachable)

    detail_calls: list[int] = []
    _stub_detail(monkeypatch, detail_calls)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    def boom_concurrent(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("LLM enrichment must not run when ollama is unreachable")

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", boom_concurrent
    )

    rc = runner.run(
        _ollama_plugin(), tmp_path, tmp_path, ai=_ollama_ai(), no_enrich=False
    )

    assert rc == 0
    # Detail enrichment still runs (plain HTTP, no ollama dependency).
    assert detail_calls == [1]
    # Row is still appended.
    rows = _read_csv(tmp_path / "jobs.csv")
    assert len(rows) == 1
    assert rows[0]["Company"] == "Acme"
    # Exactly one warning, naming the endpoint and the recovery path.
    err = capsys.readouterr().err
    assert err.lower().count("not reachable") == 1
    assert "http://localhost:11434" in err
    assert "backfill" in err.lower()


def test_preflight_unreachable_manifest_zero_enrichment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run manifest records zero LLM enrichment counters on a skipped run."""
    _stub_scrapers(monkeypatch, [_scraped_job()])

    def unreachable(endpoint: str, timeout: int = 5) -> list[str]:
        raise ollama_client.OllamaNotReachableError(f"down at {endpoint}")

    monkeypatch.setattr(ollama_client, "list_models", unreachable)
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_product_and_fit_concurrently",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    runner.run(_ollama_plugin(), tmp_path, tmp_path, ai=_ollama_ai(), no_enrich=False)

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text(encoding="utf-8"))
    assert manifest["new_jobs"] == 1
    assert manifest["enriched_fit_notes"] == 0
    assert manifest["enriched_product"] == 0


def test_preflight_model_missing_skips_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reachable but model not pulled: one warning names the model + pull command."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    _spy_list_models(monkeypatch, returns=["phi4:latest"])  # configured model absent
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_product_and_fit_concurrently",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    rc = runner.run(
        _ollama_plugin(), tmp_path, tmp_path, ai=_ollama_ai(), no_enrich=False
    )

    assert rc == 0
    err = capsys.readouterr().err
    assert "qwen2.5:14b" in err
    assert "ollama pull qwen2.5:14b" in err


def test_preflight_reachable_runs_enrichment_no_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reachable + model present: normal enrichment, no preflight warning."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])
    _stub_detail(monkeypatch, [])

    concurrent_calls: list[int] = []

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
        **_kw: Any,
    ) -> Any:
        concurrent_calls.append(len(jobs))
        return (
            jobs,
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        )

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg, "enrich_product_and_fit_concurrently", fake_concurrent
    )

    rc = runner.run(
        _ollama_plugin(), tmp_path, tmp_path, ai=_ollama_ai(), no_enrich=False
    )

    assert rc == 0
    assert concurrent_calls == [1]
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
        "enrich_product_and_fit_concurrently",
        lambda *_a, **_kw: (
            _a[0],
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        ),
    )

    # A long per-call enrich_timeout must NOT bleed into the preflight ping.
    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "ollama",
                "model": "qwen2.5:14b",
                "enrich_timeout": 120,
            },
        }
    )

    runner.run(plugin, tmp_path, tmp_path, ai=_ollama_ai(), no_enrich=False)

    assert len(calls) == 1
    assert calls[0]["timeout"] == runner._OLLAMA_PREFLIGHT_TIMEOUT
    assert calls[0]["timeout"] <= 5


def test_no_preflight_ping_on_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run performs no preflight ping (no writes, no enrichment at all)."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])

    runner.run(_ollama_plugin(), tmp_path, tmp_path, ai=_ollama_ai(), dry_run=True)

    assert calls == []


def test_no_preflight_ping_on_no_enrich(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--no-enrich performs no preflight ping (every enrichment phase is skipped)."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])

    runner.run(_ollama_plugin(), tmp_path, tmp_path, ai=_ollama_ai(), no_enrich=True)

    assert calls == []


def test_no_preflight_ping_for_claude_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claude provider never pings ollama (its which-guard already covers it)."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_product_and_fit_concurrently",
        lambda *_a, **_kw: (
            _a[0],
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        ),
    )

    claude_plugin = JobSearchPlugin.model_validate(
        {"scraper": {"enabled": True}, "enrichment": {"provider": "claude"}}
    )
    runner.run(claude_plugin, tmp_path, tmp_path, ai=AIConfig(), no_enrich=False)

    assert calls == []


def test_no_preflight_ping_when_llm_toggles_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All LLM toggles off (detail-only): no ping — detail pages need no ollama."""
    _stub_scrapers(monkeypatch, [_scraped_job()])
    calls = _spy_list_models(monkeypatch, returns=["qwen2.5:14b"])
    _stub_detail(monkeypatch, [])

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    monkeypatch.setattr(
        enrichment_pkg,
        "enrich_product_and_fit_concurrently",
        lambda *_a, **_kw: (
            _a[0],
            {"enriched": 0, "skipped_cached": 0, "failed": 0},
            {"enriched": 0, "skipped_budget": 0, "skipped_no_desc": 0, "failed": 0},
        ),
    )

    plugin = JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True},
            "enrichment": {
                "provider": "ollama",
                "model": "qwen2.5:14b",
                "enrich_product": False,
                "enrich_gd_rating": False,
                "enrich_fit": False,
                "enrich_notes": False,
            },
        }
    )
    runner.run(plugin, tmp_path, tmp_path, ai=_ollama_ai(), no_enrich=False)

    assert calls == []
