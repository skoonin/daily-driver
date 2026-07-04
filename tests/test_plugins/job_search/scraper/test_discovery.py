"""Tests for board discovery: slug-universe fetch, probes, and sweep caches.

Payload shapes mirror the live APIs (verified 2026-07-04): the greenhouse
titles-only listing (`{"jobs": [{"title": ...}]}`), the Ashby GraphQL titles
query (`{"data": {"jobBoard": {"jobPostings": [...]}}}`, ``jobBoard: null``
for an unknown org), and the aggregator slug lists (flat JSON string arrays).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import discovery
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext


def _ctx(roles: list[str] | None = None) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": roles or ["SRE"],
                "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
            }
        )
    )


def _resp(status: int, payload: Any = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    if payload is None:
        resp.json.side_effect = ValueError("no body")
    else:
        resp.json.return_value = payload
    return resp


# ── Slug universe ────────────────────────────────────────────────────────────


class TestFetchSlugUniverse:
    def test_fetch_writes_cache_and_returns_slugs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            discovery,
            "_api_request",
            lambda *a, **kw: _resp(200, ["acme", "movableink"]),
        )
        slugs, source = discovery.fetch_slug_universe(
            "greenhouse", _ctx(), MagicMock(), tmp_path
        )
        assert slugs == ["acme", "movableink"]
        assert source == "fetched"
        cached = json.loads(
            (tmp_path / "discovery" / "slugs-greenhouse.json").read_text()
        )
        assert cached["slugs"] == ["acme", "movableink"]
        assert cached["fetched_at"]

    def test_fetch_failure_falls_back_to_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "discovery").mkdir()
        (tmp_path / "discovery" / "slugs-greenhouse.json").write_text(
            json.dumps({"fetched_at": "2026-07-01", "slugs": ["cached-co"]})
        )
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: None)
        slugs, source = discovery.fetch_slug_universe(
            "greenhouse", _ctx(), MagicMock(), tmp_path
        )
        assert slugs == ["cached-co"]
        assert source == "cache"

    def test_no_upstream_no_cache_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: None)
        with pytest.raises(discovery.DiscoveryError, match="no slug list"):
            discovery.fetch_slug_universe("greenhouse", _ctx(), MagicMock(), tmp_path)

    def test_non_array_payload_falls_back_to_cache_or_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, {"not": "a list"})
        )
        with pytest.raises(discovery.DiscoveryError):
            discovery.fetch_slug_universe("greenhouse", _ctx(), MagicMock(), tmp_path)


# ── Probes ───────────────────────────────────────────────────────────────────


class TestGreenhouseProbe:
    def test_counts_matching_titles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "jobs": [
                {"title": "Lead Site Reliability Engineer"},
                {"title": "Account Executive"},
                {"title": "Senior SRE"},
            ]
        }
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, payload)
        )
        res = discovery._probe_greenhouse("movableink", _ctx(["SRE"]), MagicMock())
        assert res.outcome == "swept"
        assert res.matched == 2

    def test_404_is_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(404))
        res = discovery._probe_greenhouse("gone-co", _ctx(), MagicMock())
        assert res.outcome == "dead"

    def test_410_is_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(410))
        res = discovery._probe_greenhouse("gone-co", _ctx(), MagicMock())
        assert res.outcome == "dead"

    def test_transport_failure_is_transient_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: None)
        res = discovery._probe_greenhouse("flaky-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

    def test_exhausted_429_is_transient_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Rate limiting must NEVER enter the dead cache: the board exists.
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(429))
        res = discovery._probe_greenhouse("busy-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

    def test_corrupt_body_is_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(200))
        res = discovery._probe_greenhouse("weird-co", _ctx(), MagicMock())
        assert res.outcome == "transient"


class TestAshbyProbe:
    def test_counts_matching_titles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "data": {
                "jobBoard": {
                    "jobPostings": [
                        {"id": "1", "title": "Platform Engineer"},
                        {"id": "2", "title": "Sales Lead"},
                    ]
                }
            }
        }
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, payload)
        )
        res = discovery._probe_ashby(
            "some-co", _ctx(["Platform Engineer"]), MagicMock()
        )
        assert res.outcome == "swept"
        assert res.matched == 1

    def test_null_job_board_is_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The GraphQL endpoint answers 200 with jobBoard null for unknown orgs
        # (verified live) — that is its 404.
        monkeypatch.setattr(
            discovery,
            "_api_request",
            lambda *a, **kw: _resp(200, {"data": {"jobBoard": None}}),
        )
        res = discovery._probe_ashby("gone-co", _ctx(), MagicMock())
        assert res.outcome == "dead"

    def test_transport_failure_is_transient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: None)
        res = discovery._probe_ashby("flaky-co", _ctx(), MagicMock())
        assert res.outcome == "transient"


# ── Sweep ────────────────────────────────────────────────────────────────────


def _fake_probe_map(outcomes: dict[str, discovery.ProbeResult]) -> Any:
    def probe(slug: str, ctx: ScrapeContext, session: Any) -> discovery.ProbeResult:
        return outcomes[slug]

    return probe


def _sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slugs: list[str],
    outcomes: dict[str, discovery.ProbeResult],
    *,
    full: bool = False,
) -> discovery.PlatformSweep:
    monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(200, slugs))
    monkeypatch.setitem(discovery._PROBES, "greenhouse", _fake_probe_map(outcomes))
    return discovery.sweep_platform(
        "greenhouse",
        _ctx(),
        tmp_path,
        full=full,
        jitter=lambda: None,
    )


class TestSweepPlatform:
    def test_outcomes_land_in_the_right_caches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {
            "match-co": discovery.ProbeResult("match-co", "swept", 3),
            "nomatch-co": discovery.ProbeResult("nomatch-co", "swept", 0),
            "dead-co": discovery.ProbeResult("dead-co", "dead"),
            "flaky-co": discovery.ProbeResult("flaky-co", "transient"),
        }
        result = _sweep(tmp_path, monkeypatch, list(outcomes), outcomes)

        assert result.swept == 2
        assert result.matched_new == 1
        assert result.matched_total == 1
        assert result.dead_new == 1
        assert result.transient == 1

        matched = discovery.load_matched_boards(tmp_path, "greenhouse")
        assert set(matched) == {"match-co"}
        assert matched["match-co"]["matched"] == 3

        dead = json.loads(
            (tmp_path / "discovery" / "dead-greenhouse.json").read_text()
        )["dead"]
        assert set(dead) == {"dead-co"}
        # The transient slug is in NO cache: it must retry next sweep.
        sweep_state = json.loads(
            (tmp_path / "discovery" / "sweep-greenhouse.json").read_text()
        )["swept"]
        assert "flaky-co" not in sweep_state
        assert "flaky-co" not in dead

    def test_incremental_skips_swept_and_dead(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {
            "match-co": discovery.ProbeResult("match-co", "swept", 1),
            "dead-co": discovery.ProbeResult("dead-co", "dead"),
            "new-co": discovery.ProbeResult("new-co", "swept", 2),
        }
        _sweep(tmp_path, monkeypatch, ["match-co", "dead-co"], outcomes)
        second = _sweep(
            tmp_path, monkeypatch, ["match-co", "dead-co", "new-co"], outcomes
        )

        # Only the never-swept slug is a candidate on the incremental pass.
        assert second.candidates == 1
        assert second.swept == 1
        assert set(discovery.load_matched_boards(tmp_path, "greenhouse")) == {
            "match-co",
            "new-co",
        }

    def test_full_resweep_drops_boards_that_stopped_matching(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = {"match-co": discovery.ProbeResult("match-co", "swept", 2)}
        _sweep(tmp_path, monkeypatch, ["match-co"], first)
        assert set(discovery.load_matched_boards(tmp_path, "greenhouse")) == {
            "match-co"
        }

        second = {"match-co": discovery.ProbeResult("match-co", "swept", 0)}
        result = _sweep(tmp_path, monkeypatch, ["match-co"], second, full=True)

        assert result.candidates == 1
        assert discovery.load_matched_boards(tmp_path, "greenhouse") == {}

    def test_full_resweep_never_reprobes_dead(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {"dead-co": discovery.ProbeResult("dead-co", "dead")}
        _sweep(tmp_path, monkeypatch, ["dead-co"], outcomes)
        result = _sweep(tmp_path, monkeypatch, ["dead-co"], {}, full=True)
        assert result.candidates == 0

    def test_progress_reports_candidate_total_and_slugs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {"a": discovery.ProbeResult("a", "swept", 0)}
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, ["a"])
        )
        monkeypatch.setitem(discovery._PROBES, "greenhouse", _fake_probe_map(outcomes))
        seen: dict[str, Any] = {}

        def progress(platform: str, total: int) -> Any:
            seen["platform"] = platform
            seen["total"] = total
            return lambda slug: seen.setdefault("slugs", []).append(slug)

        discovery.sweep_platform(
            "greenhouse",
            _ctx(),
            tmp_path,
            progress=progress,
            jitter=lambda: None,
        )
        assert seen == {"platform": "greenhouse", "total": 1, "slugs": ["a"]}


class TestSweepAges:
    def test_reports_matched_counts_and_latest_stamp(self, tmp_path: Path) -> None:
        state = {
            "swept": {
                "a": {"last_swept": "2026-07-01T10:00:00", "matched": 2},
                "b": {"last_swept": "2026-07-02T10:00:00", "matched": 0},
            }
        }
        path = tmp_path / "discovery" / "sweep-greenhouse.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(state))

        ages = discovery.sweep_ages(tmp_path)
        assert ages == {
            "greenhouse": {
                "boards_matched": 1,
                "slugs_swept": 2,
                "last_swept": "2026-07-02T10:00:00",
            }
        }

    def test_empty_state_dir_reports_nothing(self, tmp_path: Path) -> None:
        assert discovery.sweep_ages(tmp_path) == {}


class TestRunDiscovery:
    def test_sweeps_each_platform_under_the_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        swept_platforms: list[str] = []

        def fake_sweep(platform: str, *a: Any, **kw: Any) -> discovery.PlatformSweep:
            swept_platforms.append(platform)
            return discovery.PlatformSweep(platform=platform)

        monkeypatch.setattr(discovery, "sweep_platform", fake_sweep)
        summary = discovery.run_discovery(
            _ctx().plugin, tmp_path, platforms=("greenhouse", "ashby")
        )
        assert swept_platforms == ["greenhouse", "ashby"]
        assert set(summary["platforms"]) == {"greenhouse", "ashby"}
        assert summary["full"] is False

    def test_stop_event_short_circuits_probes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-set stop event makes every probe return transient (no cache
        writes), so a graceful shutdown never poisons the sweep state."""
        stop = threading.Event()
        stop.set()
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, ["a", "b"])
        )
        result = discovery.sweep_platform(
            "greenhouse",
            _ctx(),
            tmp_path,
            stop_event=stop,
            jitter=lambda: None,
        )
        assert result.swept == 0
        assert result.transient == 2
        assert discovery.load_matched_boards(tmp_path, "greenhouse") == {}
