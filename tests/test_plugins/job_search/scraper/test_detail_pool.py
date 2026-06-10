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
        _job("https://boards.greenhouse.io/acme/jobs/1"),
        _job("https://boards.greenhouse.io/acme/jobs/2"),
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
    greenhouse host and the other-host fetches would wait out the backlog
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


def test_skip_paths_unchanged() -> None:
    """comp-present, inactive, url-less, and bot-walled hosts skip the fetch."""
    jobs = [
        _job("https://acme.com/job", comp="$200k"),  # already has comp
        _job("https://www.linkedin.com/jobs/view/1"),  # bot-walled host
        _job("https://news.ycombinator.com/item?id=1"),  # rate-limited host
        _job("https://ca.indeed.com/viewjob?jk=x"),  # bot-walled host
        _job(""),  # no url
        _job("https://boards.greenhouse.io/acme/jobs/9"),  # the only real fetch
    ]
    resp = MagicMock()
    resp.text = "<html></html>"
    with (
        patch(f"{_DETAIL}._api_get", return_value=resp) as api_get,
        patch(f"{_DETAIL}._parse_detail_page", return_value={}),
    ):
        _out, stats = enrich_job_details(jobs, _ctx(0))

    assert api_get.call_count == 1
    assert stats["fetched"] == 1
    assert stats["skipped"] == 5
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
