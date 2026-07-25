"""Comp "Not listed" marking: checked-but-absent pay is recorded, not blank.

Comp is tri-state: blank = not checked yet, ``COMP_NOT_LISTED`` = every
available source was checked and none states pay, value = stated pay. The mark
ends the row's comp need (no more re-fetching every backfill) while staying
upgradeable: a later real value always overwrites it, and it never overwrites
a value. A FAILED fetch proves nothing and must never mark.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import enrich_job_details
from daily_driver.plugins.job_search.scraper.enrichment.detail import (
    comp_check_pending,
)
from daily_driver.plugins.job_search.scraper.enrichment.llm import (
    _build_fit_notes_user,
)
from daily_driver.plugins.job_search.scraper.models import COMP_NOT_LISTED, EnrichedJob
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched

_DETAIL = "daily_driver.plugins.job_search.scraper.enrichment.detail"


def _ctx() -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True, "timeout": 5, "max_retries": 0},
                "enrichment": {"detail_delay_seconds": 0},
            }
        )
    )


def _job(url: str, **overrides: Any) -> EnrichedJob:
    return make_enriched(company="Acme", url=url, source="test", **overrides)


# --- Pre-pass marking (hosts with no comp fetch source) -----------------------


def test_prepass_marks_nonfetchable_host_with_payless_description() -> None:
    """Greenhouse rows never fetch: a checked description without pay is the
    row's only comp source, so Comp is conclusively not listed."""
    jobs = [
        _job("https://job-boards.greenhouse.io/acme/1", description_text="No pay here")
    ]
    with patch(f"{_DETAIL}._api_get") as api_get:
        out, stats = enrich_job_details(jobs, _ctx())
    assert api_get.call_count == 0
    assert out[0].comp == COMP_NOT_LISTED
    assert stats["comp_not_listed"] == 1
    # Marking is bookkeeping, not enrichment.
    assert stats["enriched"] == 0


def test_prepass_never_marks_without_a_description() -> None:
    """No description and no fetchable comp source: nothing was checked, so
    Comp stays blank (an Apple row heals only via re-scrape)."""
    jobs = [_job("https://jobs.apple.com/en-us/details/1/x")]
    with patch(f"{_DETAIL}._api_get") as api_get:
        out, stats = enrich_job_details(jobs, _ctx())
    assert api_get.call_count == 0
    assert out[0].comp == ""
    assert stats["comp_not_listed"] == 0


def test_prepass_fill_beats_marking() -> None:
    """A description that parses pay fills Comp; the mark is only for
    conclusively absent pay."""
    jobs = [
        _job(
            "https://job-boards.greenhouse.io/acme/2",
            description_text="Salary range: $150,000 - $180,000 USD per year.",
        )
    ]
    out, stats = enrich_job_details(jobs, _ctx())
    assert out[0].comp.startswith("$150,000")
    assert stats["comp_not_listed"] == 0


# --- Post-fetch marking (comp-serving hosts) ----------------------------------


def test_successful_empty_fetch_marks_not_listed() -> None:
    """A LinkedIn guest page that renders no salary card proves the poster
    listed no pay: the row is marked and stops being a comp need."""
    jobs = [_job("https://www.linkedin.com/jobs/view/1", description_text="No pay")]
    resp = MagicMock()
    resp.text = "<html></html>"
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp),
        patch(f"{_DETAIL}._parse_detail_page", return_value={}),
    ):
        out, stats = enrich_job_details(jobs, _ctx())
    assert out[0].comp == COMP_NOT_LISTED
    assert stats["comp_not_listed"] == 1


def test_failed_fetch_never_marks() -> None:
    """A failed fetch proves nothing: Comp stays blank so the next run
    retries instead of recording a false 'Not listed'."""
    jobs = [_job("https://www.linkedin.com/jobs/view/2", description_text="No pay")]
    with patch(f"{_DETAIL}._api_get", return_value=None):
        out, stats = enrich_job_details(jobs, _ctx())
    assert out[0].comp == ""
    assert stats["comp_not_listed"] == 0


def test_fetched_description_prose_pay_fills_same_pass() -> None:
    """A fetched description stating pay in prose (no structured salary in the
    JSON-LD) fills Comp in this pass instead of waiting for the next run."""
    details = {
        "description_text": "Great role. Salary: $120,000 - $140,000 USD annually."
    }
    jobs = [_job("https://apply.workable.com/acme/j/1")]
    resp = MagicMock()
    resp.text = "<html></html>"
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp),
        patch(f"{_DETAIL}._parse_detail_page", return_value=details),
    ):
        out, stats = enrich_job_details(jobs, _ctx())
    assert out[0].comp.startswith("$120,000")
    assert out[0].description_text == details["description_text"]
    assert stats["comp_not_listed"] == 0


# --- Sentinel semantics -------------------------------------------------------


def test_marked_row_is_not_a_comp_need() -> None:
    """A marked row no longer fetches — the whole point of the mark."""
    jobs = [
        _job(
            "https://www.linkedin.com/jobs/view/3",
            comp=COMP_NOT_LISTED,
            description_text="No pay",
        )
    ]
    with patch(f"{_DETAIL}._api_get") as api_get:
        _out, stats = enrich_job_details(jobs, _ctx())
    assert api_get.call_count == 0
    assert stats["skip_reasons"] == {"already complete": 1}


def test_real_value_overwrites_sentinel_without_force() -> None:
    """A re-scraped description that now states pay upgrades a marked row —
    the sentinel counts as absent for filling."""
    jobs = [
        _job(
            "https://job-boards.greenhouse.io/acme/3",
            comp=COMP_NOT_LISTED,
            description_text="Salary range: $150,000 - $180,000 USD per year.",
        )
    ]
    out, _stats = enrich_job_details(jobs, _ctx())
    assert out[0].comp.startswith("$150,000")


def test_prompt_treats_sentinel_as_no_comp() -> None:
    """The fit scorer never sees 'Stated compensation: Not listed'."""
    marked = _job("https://x/1", comp=COMP_NOT_LISTED)
    assert "Stated compensation" not in _build_fit_notes_user(marked, 2000)
    real = _job("https://x/2", comp="$150,000/yr")
    assert "Stated compensation: $150,000/yr" in _build_fit_notes_user(real, 2000)


# --- comp_check_pending (needs count / short-circuit) -------------------------


def test_comp_check_pending_truth_table() -> None:
    fetchable = "https://www.linkedin.com/jobs/view/9"
    blocked = "https://job-boards.greenhouse.io/acme/9"
    # Blank comp on a comp-serving host: fetch will resolve it.
    assert comp_check_pending(_job(fetchable, description_text="No pay"))
    # Blank comp, no fetchable comp source, description checked: mark pending.
    assert comp_check_pending(_job(blocked, description_text="No pay"))
    # No description and no comp source left: unresolvable, stays blank.
    assert not comp_check_pending(_job("https://jobs.apple.com/x"))
    # The pre-pass would FILL this row (description parses): not a check.
    assert not comp_check_pending(
        _job(blocked, description_text="Salary: $150,000 - $180,000 USD per year.")
    )
    # Already resolved rows are never pending.
    assert not comp_check_pending(_job(fetchable, comp="$100k"))
    assert not comp_check_pending(_job(fetchable, comp=COMP_NOT_LISTED))
    # Inactive rows are never touched.
    assert not comp_check_pending(
        _job(fetchable, status="skipped", description_text="No pay")
    )


# --- Authwall guard (marking must never trust a non-job page) -----------------


def test_authwall_page_degrades_to_failed_fetch_no_mark() -> None:
    """A LinkedIn auth-wall served with HTTP 200 has no job-page markers: the
    parse raises, the enricher records a failed fetch, and the row stays
    blank (retries next run) instead of being falsely marked."""
    jobs = [_job("https://www.linkedin.com/jobs/view/4", description_text="No pay")]
    resp = MagicMock()
    resp.text = "<html><body>Sign in to continue</body></html>"
    with patch(f"{_DETAIL}._api_get", return_value=resp):
        out, stats = enrich_job_details(jobs, _ctx())
    assert out[0].comp == ""
    assert stats["comp_not_listed"] == 0


# --- Sink board upgrades (comp_is_fillable) -----------------------------------


def test_board_upgrade_fills_over_the_mark_never_over_a_value(
    tmp_path: Any,
) -> None:
    """A board re-sighting carrying real pay overwrites a marked row's Comp;
    a row with a real value is never downgraded."""
    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
    from daily_driver.plugins.job_search.scraper.sink import _JobSink

    sink = _JobSink(
        csv_path=tmp_path / "jobs.csv",
        lock_path=tmp_path / "jobs.lock",
        header=list(CANONICAL_HEADER),
        known_urls=set(),
        known_keys=set(),
        plugin=JobSearchPlugin(),
    )
    board_job = {
        "source": "Greenhouse (acme)",
        "url": "https://job-boards.greenhouse.io/acme/1",
        "comp": "$150,000/yr",
    }
    marked = {
        "Status": "found",
        "Source": "linkedin",
        "Link": "https://www.linkedin.com/jobs/view/5",
        "Comp": COMP_NOT_LISTED,
    }
    sink._maybe_upgrade_source(marked, board_job)
    assert marked["Comp"] == "$150,000/yr"

    valued = {
        "Status": "found",
        "Source": "linkedin",
        "Link": "https://www.linkedin.com/jobs/view/6",
        "Comp": "$120,000/yr",
    }
    sink._maybe_upgrade_source(valued, board_job)
    assert valued["Comp"] == "$120,000/yr"


def test_comp_is_fillable_truth_table() -> None:
    from daily_driver.plugins.job_search.scraper.models import comp_is_fillable

    assert comp_is_fillable("")
    assert comp_is_fillable("  ")
    assert comp_is_fillable(COMP_NOT_LISTED)
    assert not comp_is_fillable("$120,000/yr")


# --- Backfill JSON contract ---------------------------------------------------


def test_backfill_dry_run_reports_comp_check_needs(tmp_path: Any) -> None:
    """The documented dry-run JSON key counts blank Comps the pass would
    resolve (here: a checked greenhouse description without pay)."""
    from daily_driver.plugins.job_search.scraper import runner
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
    )
    from tests.test_plugins.job_search.scraper.test_backfill_driver import (
        _plugin,
        _row,
        _write_jobs_csv,
    )

    csv_path = tmp_path / "jobs.csv"
    url = "https://job-boards.greenhouse.io/acme/7"
    _write_jobs_csv(csv_path, [_row(company="C", link=url, fit="8", notes="x")])
    atomic_write_descriptions(csv_path, {url: "No pay stated here."})

    summary = runner.run_backfill(_plugin(), csv_path, tmp_path, dry_run=True)
    assert summary["comp_check_needs"] == 1
    assert summary["comp_needs"] == 0


def test_backfill_summary_reports_comp_not_listed(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The completion summary carries the marked count end to end: a
    greenhouse row whose checked description states no pay gets marked by the
    real detail pass, reported in the returned dict, and written to disk."""
    from daily_driver.plugins.job_search.scraper import enrichment as enrichment_pkg
    from daily_driver.plugins.job_search.scraper import (
        runner,
    )
    from daily_driver.plugins.job_search.scraper.csv_io import read_rows
    from daily_driver.plugins.job_search.scraper.descriptions import (
        atomic_write_descriptions,
    )
    from tests.test_plugins.job_search.scraper.test_backfill_driver import (
        _plugin,
        _row,
        _write_jobs_csv,
    )

    csv_path = tmp_path / "jobs.csv"
    url = "https://job-boards.greenhouse.io/acme/8"
    _write_jobs_csv(csv_path, [_row(company="C", link=url, fit="8", notes="x")])
    atomic_write_descriptions(csv_path, {url: "No pay stated here."})

    def fake_fit_notes(jobs: list[Any], ctx: Any, **kwargs: Any) -> Any:
        return jobs, {"enriched": 0, "skipped_budget": 0, "failed": 0}

    monkeypatch.setattr(enrichment_pkg, "enrich_fit_and_notes", fake_fit_notes)

    summary = runner.run_backfill(_plugin(), csv_path, tmp_path)
    assert summary["comp_not_listed"] == 1
    _, rows = read_rows(csv_path)
    assert rows[0]["Comp"] == COMP_NOT_LISTED
