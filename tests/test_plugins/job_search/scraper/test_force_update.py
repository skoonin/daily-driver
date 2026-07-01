"""jobs backfill --force-update: re-enrich and OVERWRITE every active row.

Default backfill is fill-missing-only; ``--force-update`` re-enriches every
active row (status in ENRICH_ELIGIBLE_STATUSES, i.e. ``found``/``pending``) and
overwrites Fit, Notes, and Remote, still bounded by ``--limit`` /
``max_enrich_fit``. A cooldown (``force_recook_cooldown_hours`` / --cooldown-hours)
additionally skips rows re-enriched within the window, so an interrupted
force-update resumes instead of restarting.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
from daily_driver.plugins.job_search.scraper.enrichment import enrich_fit_and_notes
from daily_driver.plugins.job_search.scraper.enrichment.llm import _fit_notes_eligible
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


# --- Status allowlist: only found/pending are enriched -----------------------


@pytest.mark.parametrize("status", ["found", "pending"])
def test_active_funnel_statuses_are_eligible(status: str) -> None:
    j = _enriched(status=status)
    assert _fit_notes_eligible(j, force=True) is True
    assert _fit_notes_eligible(_enriched(status=status), force=False) is True


@pytest.mark.parametrize(
    "status",
    ["applied", "interviewing", "rejected", "dropped", "closed", "skipped", ""],
)
def test_triaged_or_blank_statuses_are_not_eligible(status: str) -> None:
    """Triaged and blank rows are never (re-)enriched, even under force."""
    j = _enriched(status=status, fit=0, notes="")
    assert _fit_notes_eligible(j, force=True) is False
    assert _fit_notes_eligible(j, force=False) is False


# --- Cooldown config -----------------------------------------------------------


def test_cooldown_config_default_is_24h() -> None:
    from daily_driver.plugins.job_search.config import EnrichmentConfig

    assert EnrichmentConfig().force_recook_cooldown_hours == 24


def test_cooldown_config_rejects_negative() -> None:
    from pydantic import ValidationError

    from daily_driver.plugins.job_search.config import EnrichmentConfig

    with pytest.raises(ValidationError):
        EnrichmentConfig(force_recook_cooldown_hours=-1)


# --- Force-update cooldown eligibility ---------------------------------------

_NOW = dt.datetime(2026, 6, 30, 12, 0, tzinfo=dt.timezone.utc)
_CUTOFF = _NOW - dt.timedelta(hours=24)


def test_cooldown_skips_recently_enriched_under_force() -> None:
    """A row enriched within the window is skipped so a re-run resumes."""
    fresh = _enriched(fit=8, notes="n", date_enriched=_NOW - dt.timedelta(hours=2))
    assert _fit_notes_eligible(fresh, force=True, cooldown_cutoff=_CUTOFF) is False


def test_cooldown_processes_stale_rows_under_force() -> None:
    """A row enriched before the window is still eligible."""
    stale = _enriched(fit=8, notes="n", date_enriched=_NOW - dt.timedelta(hours=30))
    assert _fit_notes_eligible(stale, force=True, cooldown_cutoff=_CUTOFF) is True


def test_cooldown_at_boundary_is_skipped() -> None:
    """A row enriched exactly at the cutoff counts as within the window."""
    boundary = _enriched(fit=8, notes="n", date_enriched=_CUTOFF)
    assert _fit_notes_eligible(boundary, force=True, cooldown_cutoff=_CUTOFF) is False


def test_never_enriched_row_is_eligible_under_force() -> None:
    """date_enriched=None (legacy / never enriched) stays eligible."""
    never = _enriched(fit=8, notes="n")
    assert never.date_enriched is None
    assert _fit_notes_eligible(never, force=True, cooldown_cutoff=_CUTOFF) is True


def test_none_cutoff_preserves_overwrite_all() -> None:
    """cooldown_cutoff=None disables the cooldown: every active row is eligible."""
    fresh = _enriched(fit=8, notes="n", date_enriched=_NOW - dt.timedelta(hours=2))
    assert _fit_notes_eligible(fresh, force=True, cooldown_cutoff=None) is True


def test_cooldown_ignored_without_force() -> None:
    """Plain backfill ignores the cutoff; a fully-filled row is still skipped."""
    fresh = _enriched(fit=8, notes="n", date_enriched=_NOW - dt.timedelta(hours=2))
    assert _fit_notes_eligible(fresh, force=False, cooldown_cutoff=_CUTOFF) is False
    empty = _enriched()
    assert _fit_notes_eligible(empty, force=False, cooldown_cutoff=_CUTOFF) is True


def test_enrich_stamps_date_enriched(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful fit/notes write stamps date_enriched (UTC-aware)."""

    def fake(prompt: str, *a: Any, **k: Any) -> str:
        return '{"fit": 7, "notes": "k8s", "remote": "hybrid"}'

    monkeypatch.setattr(ai_provider, "invoke_for", fake)
    j = _enriched(description_text="desc")
    assert j.date_enriched is None
    out, _ = enrich_fit_and_notes([j], _ctx(enrich_is_remote=True), budget=5)
    assert out[0].date_enriched is not None
    assert out[0].date_enriched.tzinfo is not None


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


def _capture_cooldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    filled: bool = True,
    **backfill_kwargs: Any,
) -> Any:
    """Run a backfill and return the cooldown_cutoff kwarg the enricher received.

    ``filled`` writes an already-enriched row (needs force to be eligible);
    ``filled=False`` writes an empty row so a plain backfill still runs.
    """
    csv_path = tmp_path / "jobs.csv"
    row = (
        _row(company="C", link="https://example.com/c", fit="8", notes="done")
        if filled
        else _row(company="C", link="https://example.com/c")
    )
    _write_jobs_csv(csv_path, [row])
    _stub_detail(monkeypatch)

    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg

    captured: dict[str, Any] = {}

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        captured["cooldown_cutoff"] = kwargs.get("cooldown_cutoff")
        return jobs, {"enriched": 0, "skipped_budget": 0, "failed": 0}

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)
    runner.run_backfill(_plugin(), csv_path, tmp_path, **backfill_kwargs)
    return captured["cooldown_cutoff"]


def test_force_resolves_config_cooldown_into_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force with the default config (24h) passes a non-None cutoff downstream."""
    cutoff = _capture_cooldown(tmp_path, monkeypatch, force=True)
    assert cutoff is not None
    assert cutoff.tzinfo is not None


def test_cooldown_hours_zero_disables_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--cooldown-hours 0 disables the cooldown (cutoff None = overwrite all)."""
    cutoff = _capture_cooldown(tmp_path, monkeypatch, force=True, cooldown_hours=0)
    assert cutoff is None


def test_no_cooldown_cutoff_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plain backfill never computes a cutoff regardless of config."""
    cutoff = _capture_cooldown(tmp_path, monkeypatch, filled=False)
    assert cutoff is None
