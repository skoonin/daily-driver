"""F3: per-host pooled detail fetches.

Politeness is per host: same-host requests stay >= delay apart, different hosts
proceed concurrently. The URL cache de-duplicates fetches. Slot replacement runs
on the calling thread in the consumer loop.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.enrichment import enrich_job_details
from daily_driver.plugins.job_search.scraper.models import EnrichedJob
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from tests.test_plugins.job_search.scraper import make_enriched

_DETAIL = "daily_driver.plugins.job_search.scraper.enrichment.detail"


def _ctx(delay: float) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "scraper": {"enabled": True, "timeout": 5, "max_retries": 0},
                "enrichment": {"detail_delay_seconds": delay},
            }
        )
    )


def _job(url: str, **overrides: Any) -> EnrichedJob:
    return make_enriched(company="Acme", url=url, source="test", **overrides)


def test_same_host_requests_spaced_at_least_delay() -> None:
    """Two fetches to the SAME host must be >= delay apart."""
    delay = 0.15
    starts: list[float] = []
    starts_lock = threading.Lock()

    def fake_api_get(session: Any, url: str, ctx: Any, **kwargs: Any) -> Any:
        with starts_lock:
            starts.append(time.monotonic())
        resp = MagicMock()
        resp.text = "<html></html>"
        return resp

    jobs = [
        _job("https://apply.workable.com/acme/j/1"),
        _job("https://apply.workable.com/acme/j/2"),
    ]
    with (
        patch(f"{_DETAIL}._api_get", side_effect=fake_api_get),
        patch(f"{_DETAIL}._parse_detail_page", return_value={}),
    ):
        enrich_job_details(jobs, _ctx(delay))

    assert len(starts) == 2
    gap = abs(starts[1] - starts[0])
    assert gap >= delay * 0.9, f"same-host gap {gap:.3f}s < delay {delay}s"


def test_different_hosts_overlap() -> None:
    """Fetches to DIFFERENT hosts must run concurrently (overlap in time)."""
    delay = 0.3
    active = [0]
    high_water = [0]
    lock = threading.Lock()

    def fake_api_get(session: Any, url: str, ctx: Any, **kwargs: Any) -> Any:
        with lock:
            active[0] += 1
            high_water[0] = max(high_water[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        resp = MagicMock()
        resp.text = "<html></html>"
        return resp

    jobs = [
        _job("https://a.example.com/1"),
        _job("https://b.example.com/2"),
        _job("https://c.example.com/3"),
    ]
    with (
        patch(f"{_DETAIL}._api_get", side_effect=fake_api_get),
        patch(f"{_DETAIL}._parse_detail_page", return_value={}),
    ):
        enrich_job_details(jobs, _ctx(delay))

    assert high_water[0] >= 2, (
        f"distinct hosts did not overlap (high-water {high_water[0]}); "
        "per-host throttle must not serialize across hosts"
    )


def test_dominant_host_does_not_block_other_hosts() -> None:
    """A dominant host's same-host backlog must not park all workers on its
    spacing: other-host fetches complete promptly, not after the backlog drains.

    8 same-host + 2 other-host jobs, 4 workers, delay 0.3s. If the throttle held
    a per-host lock across the sleep, 4 workers could all queue behind the
    dominant host and the other-host fetches would wait out the backlog
    (~0.6s+). With slot-reservation the other hosts fire immediately."""
    delay = 0.3
    finish: dict[str, float] = {}
    finish_lock = threading.Lock()
    start = time.monotonic()

    def fake_api_get(session: Any, url: str, ctx: Any, **kwargs: Any) -> Any:
        with finish_lock:
            finish[url] = time.monotonic() - start
        resp = MagicMock()
        resp.text = "<html></html>"
        return resp

    jobs = [_job(f"https://dominant.example.com/{i}") for i in range(8)]
    jobs += [
        _job("https://other-a.example.com/x"),
        _job("https://other-b.example.com/y"),
    ]
    with (
        patch(f"{_DETAIL}._api_get", side_effect=fake_api_get),
        patch(f"{_DETAIL}._parse_detail_page", return_value={}),
    ):
        enrich_job_details(jobs, _ctx(delay))

    # The two distinct other hosts have no backlog, so each fires on its first
    # (immediate) reservation — well before one full same-host spacing interval.
    other_a = finish["https://other-a.example.com/x"]
    other_b = finish["https://other-b.example.com/y"]
    assert other_a < delay, f"other-host A waited {other_a:.3f}s (>= delay {delay})"
    assert other_b < delay, f"other-host B waited {other_b:.3f}s (>= delay {delay})"


def test_cache_hit_returns_without_fetch() -> None:
    """Two jobs sharing a detail URL must trigger only ONE HTTP fetch."""
    jobs = [
        _job("https://shared.example.com/job/42"),
        _job("https://shared.example.com/job/42"),
    ]
    resp = MagicMock()
    resp.text = "<html></html>"
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp) as api_get,
        patch(f"{_DETAIL}._parse_detail_page", return_value={"comp": "$200k"}),
    ):
        out, stats = enrich_job_details(jobs, _ctx(0))

    assert api_get.call_count == 1, "shared URL must be fetched only once"
    assert stats["fetched"] == 1
    # Both jobs get the cached comp applied.
    assert all(j.comp == "$200k" for j in out)
    assert stats["enriched"] == 2


def test_fill_fields_comp_only_writes_comp_not_description() -> None:
    """The backfill path passes fill_fields={"comp"}: a detail fetch still
    fills comp but never writes description_text."""
    jobs = [_job("https://apply.workable.com/acme/j/1")]  # no comp -> fetched
    resp = MagicMock()
    resp.text = "<html></html>"
    details = {"comp": "$200k", "description_text": "Full role body."}
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp),
        patch(f"{_DETAIL}._parse_detail_page", return_value=details),
    ):
        out, stats = enrich_job_details(jobs, _ctx(0), fill_fields=frozenset({"comp"}))
    assert out[0].comp == "$200k"
    assert out[0].description_text == ""
    assert stats["enriched"] == 1  # comp still counted


def test_default_fill_fields_writes_description() -> None:
    """The run path (default fill_fields) writes description_text from the
    detail page."""
    jobs = [_job("https://apply.workable.com/acme/j/1")]
    resp = MagicMock()
    resp.text = "<html></html>"
    details = {"comp": "$200k", "description_text": "Full role body."}
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp),
        patch(f"{_DETAIL}._parse_detail_page", return_value=details),
    ):
        out, stats = enrich_job_details(jobs, _ctx(0))
    assert out[0].comp == "$200k"
    assert out[0].description_text == "Full role body."
    assert stats["descriptions_filled"] == 1


def test_description_need_alone_triggers_fetch() -> None:
    """A row with comp but no description on a JSON-LD host fetches for the
    description alone — the healing path Workable/Workday rows rely on."""
    jobs = [_job("https://apply.workable.com/acme/j/1", comp="$200k")]
    resp = MagicMock()
    resp.text = "<html></html>"
    details = {"comp": "$180k", "description_text": "Full role body."}
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp) as api_get,
        patch(f"{_DETAIL}._parse_detail_page", return_value=details),
    ):
        out, stats = enrich_job_details(jobs, _ctx(0))
    assert api_get.call_count == 1
    # Fill-only: the fetched comp never overwrites the existing value.
    assert out[0].comp == "$200k"
    assert out[0].description_text == "Full role body."
    assert stats["descriptions_filled"] == 1


def test_linkedin_missing_description_only_does_not_fetch() -> None:
    """LinkedIn serves comp only (salary card): a row missing just its
    description has no fillable need there, so no request is spent."""
    jobs = [_job("https://www.linkedin.com/jobs/view/1", comp="$200k")]
    with (
        patch(f"{_DETAIL}._api_get") as api_get,
        patch(f"{_DETAIL}._parse_detail_page", return_value={}),
    ):
        _out, stats = enrich_job_details(jobs, _ctx(0))
    assert api_get.call_count == 0
    assert stats["skip_reasons"] == {"already complete": 1}


def test_capability_matches_host_suffix_not_substring() -> None:
    """Host capability is exact/suffix keyed: lookalike hosts must fall through
    to the default full-JSON-LD capability, and subdomains must inherit."""
    from daily_driver.plugins.job_search.scraper.enrichment.detail import (
        DETAIL_FIELDS,
        _capability_for,
    )

    assert _capability_for("https://www.linkedin.com/jobs/view/1").fields == (
        frozenset({"comp"})
    )
    assert _capability_for("https://evillinkedin.com/x").fields == DETAIL_FIELDS
    assert _capability_for("https://linkedin.com.evil.example/x").fields == (
        DETAIL_FIELDS
    )
    assert _capability_for("https://job-boards.greenhouse.io/acme").fields == (
        frozenset()
    )


def test_skip_paths_unchanged() -> None:
    """complete, inactive, url-less, and bot-walled hosts skip the fetch;
    LinkedIn fetches (guest salary card)."""
    jobs = [
        _job("https://acme.com/job", comp="$200k", description_text="d"),  # complete
        _job("https://www.linkedin.com/jobs/view/1"),  # fetched: salary card
        _job("https://news.ycombinator.com/item?id=1"),  # rate-limited host
        _job("https://ca.indeed.com/viewjob?jk=x"),  # bot-walled host
        _job(""),  # no url
        _job("https://apply.workable.com/acme/j/9"),  # real fetch
    ]
    resp = MagicMock()
    resp.text = "<html></html>"
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp) as api_get,
        patch(f"{_DETAIL}._parse_detail_page", return_value={}),
    ):
        _out, stats = enrich_job_details(jobs, _ctx(0))

    assert api_get.call_count == 2
    assert stats["fetched"] == 2
    assert stats["skipped"] == 4
    assert stats["total"] == 6


def test_slot_replacement_on_calling_thread() -> None:
    """The consumer loop applies results; in-place slot replacement and progress
    happen on the calling (main) thread, never in a worker."""
    apply_threads: list[str] = []

    def record_progress(n: int, detail: str | None) -> None:
        apply_threads.append(threading.current_thread().name)

    resp = MagicMock()
    resp.text = "<html></html>"
    jobs = [
        _job("https://a.example.com/1"),
        _job("https://b.example.com/2"),
    ]
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp),
        patch(f"{_DETAIL}._parse_detail_page", return_value={"comp": "$100k"}),
    ):
        enrich_job_details(jobs, _ctx(0), progress=record_progress)

    assert apply_threads
    assert all(n == "MainThread" for n in apply_threads), apply_threads


# ---------------------------------------------------------------------------
# Skip-reason breakdown (PART B.1): tally why each job was skipped.
# ---------------------------------------------------------------------------


def test_skip_reason_breakdown_tallied() -> None:
    """stats['skip_reasons'] tallies per-reason; the counts sum to skipped."""
    jobs = [
        _job("https://acme.com/job", comp="$200k", description_text="d"),
        _job("https://acme.com/job2", comp="$150k", description_text="d"),
        _job("https://news.ycombinator.com/item?id=1"),  # hn: rate-limited
        _job("https://ca.indeed.com/viewjob?jk=x"),  # indeed: bot-walled
        _job("https://jobs.apple.com/en-us/details/1/x"),  # apple: SPA, no JSON-LD
        _job(""),  # no url
        _job("https://x.com/j", status="skipped"),  # inactive
        _job("https://apply.workable.com/acme/j/9"),  # real fetch
    ]
    resp = MagicMock()
    resp.text = "<html></html>"
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp),
        patch(f"{_DETAIL}._parse_detail_page", return_value={}),
    ):
        _out, stats = enrich_job_details(jobs, _ctx(0))

    reasons = stats["skip_reasons"]
    assert sum(reasons.values()) == stats["skipped"] == 7
    assert reasons["already complete"] == 2
    assert reasons["hn: rate-limited"] == 1
    assert reasons["indeed: bot-walled"] == 1
    assert reasons["apple: SPA, no server JSON-LD"] == 1
    assert reasons["no url"] == 1
    assert reasons["inactive"] == 1


def test_render_skip_breakdown_string() -> None:
    """The detail phase summary renders e.g. '0 enriched, 7 skipped (...)'."""
    from daily_driver.plugins.job_search.scraper.enrichment.detail import (
        render_detail_summary,
    )

    stats = {
        "enriched": 0,
        "skipped": 7,
        "total": 7,
        "fetched": 0,
        "skip_reasons": {"already complete": 5, "indeed: bot-walled": 2},
    }
    out = render_detail_summary(stats)
    assert out == "0 enriched, 7 skipped (5 already complete, 2 indeed: bot-walled)"


def test_render_skip_breakdown_no_skips() -> None:
    from daily_driver.plugins.job_search.scraper.enrichment.detail import (
        render_detail_summary,
    )

    stats = {
        "enriched": 3,
        "skipped": 0,
        "total": 3,
        "fetched": 3,
        "skip_reasons": {},
    }
    assert render_detail_summary(stats) == "3 enriched, 0 skipped"


# ---------------------------------------------------------------------------
# force=True: recompute comp from the cached description (backfill repair).
# ---------------------------------------------------------------------------

_DESC_WITH_RANGE = (
    "The standard base pay range for this role is "
    "$100,220\\.00 \\- $197,758\\.00 CAD."
)


def test_force_recomputes_comp_from_cached_description() -> None:
    """A row holding a mis-extracted comp is overwritten when the cached
    description parses to a different value; no HTTP is issued."""
    jobs = [
        _job(
            "https://www.linkedin.com/jobs/view/1",
            comp="$100,220/yr",
            description_text=_DESC_WITH_RANGE,
        )
    ]
    with patch(f"{_DETAIL}._api_get") as api_get:
        out, stats = enrich_job_details(jobs, _ctx(0), force=True)

    api_get.assert_not_called()
    assert out[0].comp == "$100,220–$197,758/yr CAD"
    assert stats["comp_recomputed"] == 1
    assert stats["enriched"] == 1


def test_force_never_blanks_comp_when_description_yields_nothing() -> None:
    """force must not erase an existing comp because the description no longer
    parses; the row skips as 'already complete' with no fetch."""
    jobs = [
        _job(
            "https://www.linkedin.com/jobs/view/1",
            comp="$150,000/yr",
            description_text="A great role on a great team.",
        )
    ]
    with patch(f"{_DETAIL}._api_get") as api_get:
        out, stats = enrich_job_details(jobs, _ctx(0), force=True)

    api_get.assert_not_called()
    assert out[0].comp == "$150,000/yr"
    assert stats["comp_recomputed"] == 0
    assert stats["skip_reasons"] == {"already complete": 1}


def test_force_leaves_rows_without_cached_description_untouched() -> None:
    """force reads the description cache only: a comp-holding row with no
    cached description keeps its value and triggers no fetch."""
    jobs = [_job("https://www.linkedin.com/jobs/view/1", comp="$150,000/yr")]
    with patch(f"{_DETAIL}._api_get") as api_get:
        out, stats = enrich_job_details(jobs, _ctx(0), force=True)

    api_get.assert_not_called()
    assert out[0].comp == "$150,000/yr"
    assert stats["comp_recomputed"] == 0


def test_force_default_false_keeps_existing_comp() -> None:
    """Without force, a row with comp is never re-parsed (run-path behavior)."""
    jobs = [
        _job(
            "https://www.linkedin.com/jobs/view/1",
            comp="$100,220/yr",
            description_text=_DESC_WITH_RANGE,
        )
    ]
    with patch(f"{_DETAIL}._api_get") as api_get:
        out, stats = enrich_job_details(jobs, _ctx(0))

    api_get.assert_not_called()
    assert out[0].comp == "$100,220/yr"
    assert stats["comp_recomputed"] == 0


def test_render_summary_includes_comp_recomputed() -> None:
    from daily_driver.plugins.job_search.scraper.enrichment.detail import (
        render_detail_summary,
    )

    stats = {
        "enriched": 4,
        "from_description": 1,
        "comp_recomputed": 3,
        "skipped": 0,
        "total": 4,
        "fetched": 0,
        "skip_reasons": {},
    }
    assert render_detail_summary(stats) == (
        "4 enriched (1 from cached descriptions, 3 comp repaired), 0 skipped"
    )
