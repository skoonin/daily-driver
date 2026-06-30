"""jobs backfill --force-update: re-enrich and OVERWRITE every active row.

Default backfill is fill-missing-only; ``--force-update`` re-enriches every
active row (status not in ENRICH_SKIP_STATUSES) and overwrites Fit, Notes, and
Remote, still bounded by ``--limit`` / ``max_enrich_fit``.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
from daily_driver.plugins.job_search.scraper.enrichment import enrich_fit_and_notes
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    NormalizedJob,
    RawScrapedJob,
)
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

# --- Enricher-level overwrite semantics --------------------------------------


def _enriched(**overrides: object) -> EnrichedJob:
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="remoteok",
        location="Berlin, Germany",
    )
    base = EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))
    return base.model_copy(update=dict(overrides))


def _ctx(**enrichment: object) -> ScrapeContext:
    base: dict[str, object] = {
        "provider": "ollama",
        "model": "qwen2.5:14b",
        "max_enrich_fit": 5,
        "enrich_timeout": 5,
    }
    base.update(enrichment)
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate({"enrichment": base}),
        ai=AIConfig(),
    )


def test_force_overwrites_existing_fit_notes_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force=True overwrites a fully-filled active row's Fit, Notes, and Remote."""

    def fake(prompt: str, *a: Any, **k: Any) -> str:
        return '{"fit": 7, "notes": "k8s", "remote": "hybrid"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake)
    j = _enriched(fit=3, notes="old notes", remote="onsite", description_text="desc")
    out, stats = enrich_fit_and_notes(
        [j], _ctx(enrich_is_remote=True), budget=5, force=True
    )
    assert out[0].fit == 7
    assert out[0].notes == "k8s"
    assert out[0].remote == "hybrid"
    assert stats["enriched"] == 1


def test_default_leaves_existing_values_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without force, a fully-filled active row is not eligible: nothing changes."""

    def fake(prompt: str, *a: Any, **k: Any) -> str:
        return '{"fit": 7, "notes": "k8s", "remote": "hybrid"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake)
    j = _enriched(fit=3, notes="old notes", remote="onsite", description_text="desc")
    out, stats = enrich_fit_and_notes([j], _ctx(enrich_is_remote=True), budget=5)
    assert out[0].fit == 3
    assert out[0].notes == "old notes"
    assert out[0].remote == "onsite"
    assert stats["enriched"] == 0


def test_force_still_skips_enrich_skip_status_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force never re-enriches an ENRICH_SKIP-status row (eligibility floor)."""

    def fake(prompt: str, *a: Any, **k: Any) -> str:
        return '{"fit": 7, "notes": "k8s", "remote": "hybrid"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake)
    j = _enriched(
        status="skipped",
        fit=3,
        notes="old notes",
        remote="onsite",
        description_text="desc",
    )
    out, stats = enrich_fit_and_notes(
        [j], _ctx(enrich_is_remote=True), budget=5, force=True
    )
    assert out[0].fit == 3
    assert out[0].notes == "old notes"
    assert out[0].remote == "onsite"
    assert stats["enriched"] == 0


# --- Driver-level threading (run_backfill -> enrich_fit_and_notes) -----------


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


def _row(
    *,
    company: str,
    link: str,
    status: str = "found",
    fit: str = "",
    notes: str = "",
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
        "Source": "remoteok",
    }


def _plugin(max_enrich_fit: int = 50) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "scraper": {"enabled": True, "timeout": 5, "max_retries": 1},
            "enrichment": {
                "max_enrich_fit": max_enrich_fit,
                "detail_delay_seconds": 0,
            },
        }
    )


def _stub_detail(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_run_backfill_threads_force_into_enricher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force=True reaches enrich_fit_and_notes even for an already-filled row."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [_row(company="C", link="https://example.com/c", fit="8", notes="done")],
    )
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    captured: dict[str, Any] = {}

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        captured["force"] = kwargs.get("force")
        return jobs, {"enriched": 0, "skipped_budget": 0, "failed": 0}

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path, force=True)

    assert captured["force"] is True


def test_run_backfill_default_force_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the flag, the enricher receives force=False (fill-missing-only)."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(csv_path, [_row(company="C", link="https://example.com/c")])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    captured: dict[str, Any] = {}

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        captured["force"] = kwargs.get("force")
        return jobs, {"enriched": 0, "skipped_budget": 0, "failed": 0}

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path)

    assert captured["force"] is False


def test_run_backfill_force_does_not_short_circuit_when_all_filled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force counts every active row, so a fully-filled file still enriches."""
    csv_path = tmp_path / "jobs.csv"
    _write_jobs_csv(
        csv_path,
        [_row(company="C", link="https://example.com/c", fit="8", notes="done")],
    )
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    ran: list[bool] = []

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        ran.append(True)
        return jobs, {"enriched": 0, "skipped_budget": 0, "failed": 0}

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    runner.run_backfill(_plugin(), csv_path, tmp_path, force=True)

    assert ran == [True]
