"""Tests for board discovery: slug-universe fetch, probes, and sweep caches.

Payload shapes mirror the live APIs (verified 2026-07-04): the greenhouse
titles-only listing (`{"jobs": [{"title": ...}]}`), the Ashby GraphQL titles
query (`{"data": {"jobBoard": {"jobPostings": [...]}}}`, ``jobBoard: null``
for an unknown org), the Lever postings endpoint (a bare JSON array; ``[]``
for a live-but-empty board, 404 for a gone slug), and the aggregator slug
lists (flat JSON string arrays).
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from daily_driver.core.clock import now
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import discovery
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext


def _ctx(
    roles: list[str] | None = None,
    reprobe_days: int | None = None,
    max_reprobe_per_sweep: int | None = None,
    dormant_after_empty_sweeps: int | None = None,
    dormant_reprobe_multiplier: int | None = None,
) -> ScrapeContext:
    payload: dict[str, Any] = {
        "roles": roles or ["SRE"],
        "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
    }
    discovery_cfg = {
        key: value
        for key, value in (
            ("reprobe_days", reprobe_days),
            ("max_reprobe_per_sweep", max_reprobe_per_sweep),
            ("dormant_after_empty_sweeps", dormant_after_empty_sweeps),
            ("dormant_reprobe_multiplier", dormant_reprobe_multiplier),
        )
        if value is not None
    }
    if discovery_cfg:
        payload["discovery"] = discovery_cfg
    return ScrapeContext(plugin=JobSearchPlugin.model_validate(payload))


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
        assert res.total == 3

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

    def test_non_list_body_is_transient_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed 200 body must not persist a bogus total (dict keys,
        string chars); it retries next sweep like any other broken fetch."""
        monkeypatch.setattr(
            discovery,
            "_api_request",
            lambda *a, **kw: _resp(200, {"jobs": {"error": "oops"}}),
        )
        res = discovery._probe_greenhouse("weird-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

    def test_empty_board_is_swept_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A live board with no openings answers 200 with an empty list
        # (Ashby/Lever verified live 2026-08-30; greenhouse same shape).
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, {"jobs": []})
        )
        res = discovery._probe_greenhouse("quiet-co", _ctx(), MagicMock())
        assert res.outcome == "swept"
        assert res.matched == 0
        assert res.total == 0


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
        assert res.total == 2

    def test_empty_board_is_swept_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The snyk case (verified live 2026-08-30): org exists, zero postings.
        payload = {"data": {"jobBoard": {"jobPostings": []}}}
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, payload)
        )
        res = discovery._probe_ashby("quiet-co", _ctx(), MagicMock())
        assert res.outcome == "swept"
        assert res.matched == 0
        assert res.total == 0

    def test_null_postings_on_live_board_counts_as_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A present jobBoard whose jobPostings is null is an empty board,
        not a dead one and not a probe failure."""
        payload = {"data": {"jobBoard": {"jobPostings": None}}}
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, payload)
        )
        res = discovery._probe_ashby("quiet-co", _ctx(), MagicMock())
        assert res.outcome == "swept"
        assert res.matched == 0
        assert res.total == 0

    def test_non_list_postings_is_transient_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A truthy non-list jobPostings survives the `or []` null guard; it
        must read as a broken fetch, not a board with key-count postings."""
        payload = {"data": {"jobBoard": {"jobPostings": {"error": "oops"}}}}
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, payload)
        )
        res = discovery._probe_ashby("weird-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

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

    def test_graphql_error_response_is_transient_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GraphQL resolver failures arrive as HTTP 200 with `errors` and a
        null top-level `data`. Misreading that as dead would permanently drop
        a live board from every future sweep (dead-cache poisoning)."""
        monkeypatch.setattr(
            discovery,
            "_api_request",
            lambda *a, **kw: _resp(
                200, {"data": None, "errors": [{"message": "boom"}]}
            ),
        )
        res = discovery._probe_ashby("live-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

    def test_graphql_errors_with_data_present_is_transient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Partial-error responses (errors alongside data) are not trustworthy
        # enough to declare a board dead OR record a match count.
        monkeypatch.setattr(
            discovery,
            "_api_request",
            lambda *a, **kw: _resp(
                200,
                {"data": {"jobBoard": None}, "errors": [{"message": "rate limited"}]},
            ),
        )
        res = discovery._probe_ashby("live-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

    def test_transport_failure_is_transient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: None)
        res = discovery._probe_ashby("flaky-co", _ctx(), MagicMock())
        assert res.outcome == "transient"


def test_sweep_platform_registries_stay_in_sync() -> None:
    """Every swept platform needs all three per-platform seams: a slug-list
    URL and a probe (KeyError mid-sweep if missing) and an explicit worker cap
    (a missing cap silently falls back to 10 instead of failing loudly)."""
    platforms = set(discovery.SWEEP_PLATFORMS)
    assert set(discovery._SLUG_LIST_URLS) == platforms
    assert set(discovery._PROBES) == platforms
    assert set(discovery._WORKER_CAPS) == platforms


class TestLeverProbe:
    def test_counts_matching_titles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = [
            {"text": "Senior Site Reliability Engineer"},
            {"text": "Account Executive"},
            {"text": "Staff SRE"},
        ]
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, payload)
        )
        res = discovery._probe_lever("some-co", _ctx(["SRE"]), MagicMock())
        assert res.outcome == "swept"
        assert res.matched == 2
        assert res.total == 3

    def test_empty_board_is_swept_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A live board with nothing open returns [] (verified live); only
        # 404/410 means the slug is gone.
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(200, []))
        res = discovery._probe_lever("quiet-co", _ctx(), MagicMock())
        assert res.outcome == "swept"
        assert res.matched == 0
        assert res.total == 0

    def test_404_is_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(404))
        res = discovery._probe_lever("gone-co", _ctx(), MagicMock())
        assert res.outcome == "dead"

    def test_410_is_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(410))
        res = discovery._probe_lever("gone-co", _ctx(), MagicMock())
        assert res.outcome == "dead"

    def test_exhausted_429_is_transient_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(429))
        res = discovery._probe_lever("busy-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

    def test_transport_failure_is_transient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: None)
        res = discovery._probe_lever("flaky-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

    def test_corrupt_body_is_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(200))
        res = discovery._probe_lever("weird-co", _ctx(), MagicMock())
        assert res.outcome == "transient"

    def test_non_list_body_is_transient_not_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An error object served with HTTP 200 is a broken fetch, never a
        # permanent-dead verdict (dead-cache poisoning).
        monkeypatch.setattr(
            discovery,
            "_api_request",
            lambda *a, **kw: _resp(200, {"ok": False, "error": "oops"}),
        )
        res = discovery._probe_lever("live-co", _ctx(), MagicMock())
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
    ctx: ScrapeContext | None = None,
) -> discovery.PlatformSweep:
    monkeypatch.setattr(discovery, "_api_request", lambda *a, **kw: _resp(200, slugs))
    monkeypatch.setitem(discovery._PROBES, "greenhouse", _fake_probe_map(outcomes))
    return discovery.sweep_platform(
        "greenhouse",
        ctx or _ctx(),
        tmp_path,
        full=full,
        jitter=lambda: None,
    )


def _age_sweep_stamps(tmp_path: Path, days: int) -> None:
    """Backdate every recorded last_swept stamp, simulating an old sweep."""
    path = tmp_path / "discovery" / "sweep-greenhouse.json"
    payload = json.loads(path.read_text())
    stamp = (now() - timedelta(days=days)).isoformat()
    for info in payload["swept"].values():
        info["last_swept"] = stamp
    path.write_text(json.dumps(payload))


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

    def test_swept_entry_records_total(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cache must tell an empty board (total 0) from a populated one
        whose roles merely don't match (total > 0) — both have matched 0."""
        outcomes = {
            "nomatch-co": discovery.ProbeResult("nomatch-co", "swept", 0, total=9),
            "quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0),
        }
        _sweep(tmp_path, monkeypatch, list(outcomes), outcomes)

        swept = json.loads(
            (tmp_path / "discovery" / "sweep-greenhouse.json").read_text()
        )["swept"]
        assert swept["nomatch-co"]["total"] == 9
        assert swept["quiet-co"]["total"] == 0

    def test_stale_board_gone_empty_leaves_matched_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 200-empty analog of the #214 404 case: a matched board whose
        listing empties out (moved ATS) retires from the scrape list on its
        stale re-probe, and the cache records that it is truly empty."""
        _sweep(
            tmp_path,
            monkeypatch,
            ["match-co"],
            {"match-co": discovery.ProbeResult("match-co", "swept", 4, total=12)},
        )
        _age_sweep_stamps(tmp_path, 45)

        result = _sweep(
            tmp_path,
            monkeypatch,
            ["match-co"],
            {"match-co": discovery.ProbeResult("match-co", "swept", 0, total=0)},
        )

        assert result.restaled == 1
        assert discovery.load_matched_boards(tmp_path, "greenhouse") == {}
        entry = json.loads(
            (tmp_path / "discovery" / "sweep-greenhouse.json").read_text()
        )["swept"]["match-co"]
        assert entry["matched"] == 0
        assert entry["total"] == 0

    def test_entry_without_total_reads_without_error(self, tmp_path: Path) -> None:
        """Pre-total cache entries (two keys) must load fine; total is absent,
        never assumed zero. Entries refresh to the new shape on re-probe."""
        path = tmp_path / "discovery" / "sweep-greenhouse.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {"swept": {"old-co": {"last_swept": now().isoformat(), "matched": 2}}}
            )
        )

        assert set(discovery.load_matched_boards(tmp_path, "greenhouse")) == {"old-co"}
        assert discovery.sweep_ages(tmp_path)["greenhouse"]["boards_matched"] == 1

    def test_incremental_reprobes_stale_slugs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {"match-co": discovery.ProbeResult("match-co", "swept", 1)}
        _sweep(tmp_path, monkeypatch, ["match-co"], outcomes)
        _age_sweep_stamps(tmp_path, 31)

        second = _sweep(tmp_path, monkeypatch, ["match-co"], outcomes)

        assert second.candidates == 1
        assert second.restaled == 1

    def test_stale_board_that_died_retires_without_full(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The #214 case: a board matched, then 404s, must leave the scrape list."""
        _sweep(
            tmp_path,
            monkeypatch,
            ["match-co"],
            {"match-co": discovery.ProbeResult("match-co", "swept", 4)},
        )
        assert set(discovery.load_matched_boards(tmp_path, "greenhouse")) == {
            "match-co"
        }
        _age_sweep_stamps(tmp_path, 45)

        result = _sweep(
            tmp_path,
            monkeypatch,
            ["match-co"],
            {"match-co": discovery.ProbeResult("match-co", "dead")},
        )

        assert result.dead_new == 1
        assert discovery.load_matched_boards(tmp_path, "greenhouse") == {}

    def test_fresh_slugs_are_not_reprobed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {"match-co": discovery.ProbeResult("match-co", "swept", 1)}
        _sweep(tmp_path, monkeypatch, ["match-co"], outcomes)
        _age_sweep_stamps(tmp_path, 29)

        second = _sweep(tmp_path, monkeypatch, ["match-co"], outcomes)

        assert second.candidates == 0
        assert second.restaled == 0

    def test_reprobe_days_knob_sets_the_staleness_threshold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {"match-co": discovery.ProbeResult("match-co", "swept", 1)}
        _sweep(tmp_path, monkeypatch, ["match-co"], outcomes)
        _age_sweep_stamps(tmp_path, 8)

        second = _sweep(
            tmp_path, monkeypatch, ["match-co"], outcomes, ctx=_ctx(reprobe_days=7)
        )

        assert second.candidates == 1

    def test_stale_reprobes_are_capped_oldest_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One sweep stamps every slug the same day, so an uncapped threshold
        would re-probe the whole universe at once and repeat that herd every
        reprobe_days. The cap takes the oldest slugs and defers the rest.
        """
        slugs = ["a-co", "b-co", "c-co"]
        outcomes = {slug: discovery.ProbeResult(slug, "swept", 1) for slug in slugs}
        _sweep(tmp_path, monkeypatch, slugs, outcomes)

        path = tmp_path / "discovery" / "sweep-greenhouse.json"
        payload = json.loads(path.read_text())
        for offset, slug in enumerate(slugs):
            payload["swept"][slug]["last_swept"] = (
                now() - timedelta(days=40 - offset)
            ).isoformat()
        path.write_text(json.dumps(payload))

        second = _sweep(
            tmp_path,
            monkeypatch,
            slugs,
            outcomes,
            ctx=_ctx(max_reprobe_per_sweep=2),
        )

        assert second.candidates == 2
        assert second.restaled == 2

    def test_unparseable_last_swept_counts_as_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {"match-co": discovery.ProbeResult("match-co", "swept", 1)}
        _sweep(tmp_path, monkeypatch, ["match-co"], outcomes)
        path = tmp_path / "discovery" / "sweep-greenhouse.json"
        payload = json.loads(path.read_text())
        payload["swept"]["match-co"]["last_swept"] = "not-a-timestamp"
        path.write_text(json.dumps(payload))

        second = _sweep(tmp_path, monkeypatch, ["match-co"], outcomes)

        assert second.candidates == 1

    def test_full_resweep_never_reprobes_dead(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {"dead-co": discovery.ProbeResult("dead-co", "dead")}
        _sweep(tmp_path, monkeypatch, ["dead-co"], outcomes)
        result = _sweep(tmp_path, monkeypatch, ["dead-co"], {}, full=True)
        assert result.candidates == 0

    def test_progress_reports_candidate_total_and_advances(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes = {"a": discovery.ProbeResult("a", "swept", 0)}
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, ["a"])
        )
        monkeypatch.setitem(discovery._PROBES, "greenhouse", _fake_probe_map(outcomes))
        seen: dict[str, Any] = {"advanced": 0}

        def progress(platform: str, total: int) -> Any:
            seen["platform"] = platform
            seen["total"] = total

            def advance() -> None:
                seen["advanced"] += 1

            return advance

        discovery.sweep_platform(
            "greenhouse",
            _ctx(),
            tmp_path,
            progress=progress,
            jitter=lambda: None,
        )
        assert seen == {"platform": "greenhouse", "total": 1, "advanced": 1}

    def test_duplicate_upstream_slugs_probed_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def probe(slug: str, ctx: ScrapeContext, session: Any) -> discovery.ProbeResult:
            calls.append(slug)
            return discovery.ProbeResult(slug, "swept", 1)

        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, ["dup", "dup"])
        )
        monkeypatch.setitem(discovery._PROBES, "greenhouse", probe)
        result = discovery.sweep_platform(
            "greenhouse", _ctx(), tmp_path, jitter=lambda: None
        )
        assert calls == ["dup"]
        assert result.candidates == 1
        assert result.matched_new == 1

    def test_periodic_flush_persists_before_sweep_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mid-sweep flush is the basis of the interruption-safety
        promise; with _FLUSH_EVERY forced to 1, every recorded probe must hit
        disk before the terminal flush."""
        monkeypatch.setattr(discovery, "_FLUSH_EVERY", 1)
        flushes: list[int] = []
        real_write = discovery._write_json

        def counting_write(path: Path, payload: dict[str, Any]) -> None:
            flushes.append(1)
            real_write(path, payload)

        monkeypatch.setattr(discovery, "_write_json", counting_write)
        outcomes = {
            "a": discovery.ProbeResult("a", "swept", 1),
            "b": discovery.ProbeResult("b", "swept", 0),
        }
        _sweep(tmp_path, monkeypatch, ["a", "b"], outcomes)
        # 1 slug-cache write + (2 per-record flushes + 1 terminal flush) x 2
        # files each: strictly more than the terminal flush alone would make.
        assert len(flushes) >= 6

    def test_keyboard_interrupt_flushes_recorded_probes_and_reraises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl-C mid-sweep must keep already-recorded outcomes on disk (the
        resume contract) and re-raise for the CLI's exit-code mapping."""
        monkeypatch.setattr(
            discovery, "_api_request", lambda *a, **kw: _resp(200, ["ok", "boom"])
        )

        def probe(slug: str, ctx: ScrapeContext, session: Any) -> discovery.ProbeResult:
            if slug == "boom":
                raise KeyboardInterrupt
            return discovery.ProbeResult(slug, "swept", 2)

        monkeypatch.setitem(discovery._PROBES, "greenhouse", probe)
        with pytest.raises(KeyboardInterrupt):
            discovery.sweep_platform(
                "greenhouse", _ctx(), tmp_path, jitter=lambda: None
            )
        # The interrupt escaped through fut.result(); the finally-flush must
        # still have written whatever was recorded before it. Depending on
        # completion order "ok" may or may not have been recorded, but the
        # sweep file itself must exist and parse.
        state = json.loads(
            (tmp_path / "discovery" / "sweep-greenhouse.json").read_text()
        )
        assert "swept" in state


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
                "slugs_dead": 0,
                "universe": 0,
                "never_probed": 0,
                "last_swept": "2026-07-02T10:00:00",
            }
        }

    def test_empty_state_dir_reports_nothing(self, tmp_path: Path) -> None:
        assert discovery.sweep_ages(tmp_path) == {}

    def test_never_probed_is_the_universe_minus_swept_and_dead(
        self, tmp_path: Path
    ) -> None:
        """The #213 coverage gap: a slug in neither cache has never been looked
        at, so `jobs run` cannot scrape it and nothing else reports it."""
        discovery_dir = tmp_path / "discovery"
        discovery_dir.mkdir(parents=True)
        (discovery_dir / "sweep-greenhouse.json").write_text(
            json.dumps(
                {"swept": {"a": {"last_swept": "2026-07-01T10:00:00", "matched": 1}}}
            )
        )
        (discovery_dir / "dead-greenhouse.json").write_text(
            json.dumps({"dead": {"b": "2026-07-01T10:00:00"}})
        )
        (discovery_dir / "slugs-greenhouse.json").write_text(
            json.dumps({"fetched_at": "2026-07-01", "slugs": ["a", "b", "c", "d"]})
        )

        stats = discovery.sweep_ages(tmp_path)["greenhouse"]

        assert stats["universe"] == 4
        assert stats["slugs_dead"] == 1
        assert stats["never_probed"] == 2

    def test_never_probed_never_goes_negative(self, tmp_path: Path) -> None:
        """The upstream slug list can shrink below what was already swept."""
        discovery_dir = tmp_path / "discovery"
        discovery_dir.mkdir(parents=True)
        (discovery_dir / "sweep-greenhouse.json").write_text(
            json.dumps(
                {
                    "swept": {
                        "a": {"last_swept": "2026-07-01T10:00:00", "matched": 1},
                        "b": {"last_swept": "2026-07-01T10:00:00", "matched": 1},
                    }
                }
            )
        )
        (discovery_dir / "slugs-greenhouse.json").write_text(
            json.dumps({"fetched_at": "2026-07-01", "slugs": ["a"]})
        )

        assert discovery.sweep_ages(tmp_path)["greenhouse"]["never_probed"] == 0


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


# ── Board resolution (jobs run seam) ─────────────────────────────────────────


class TestResolveBoards:
    def test_union_pins_first_then_discovered(self) -> None:
        assert discovery.resolve_boards(
            "greenhouse", ["pin-a", "pin-b"], ("disc-a", "disc-b"), []
        ) == ["pin-a", "pin-b", "disc-a", "disc-b"]

    def test_pin_discovered_overlap_dedups(self) -> None:
        assert discovery.resolve_boards(
            "greenhouse", ["both"], ("both", "disc"), []
        ) == [
            "both",
            "disc",
        ]

    def test_exclude_trumps_pins_and_discovered(self) -> None:
        assert discovery.resolve_boards(
            "greenhouse", ["pin", "noisy"], ("noisy", "disc"), ["noisy"]
        ) == ["pin", "disc"]

    def test_empty_everything_warns(self, caplog: Any) -> None:
        with caplog.at_level("WARNING"):
            assert discovery.resolve_boards("lever", [], (), []) == []
        assert "[lever] no boards to scrape" in caplog.text
        assert "jobs discover-boards" in caplog.text

    def test_non_empty_does_not_warn(self, caplog: Any) -> None:
        with caplog.at_level("WARNING"):
            discovery.resolve_boards("lever", ["pin"], (), [])
        assert "no boards to scrape" not in caplog.text


# ── Dormancy backoff ─────────────────────────────────────────────────────────


def _swept_entry(tmp_path: Path, slug: str) -> dict[str, Any]:
    path = tmp_path / "discovery" / "sweep-greenhouse.json"
    entry: dict[str, Any] = dict(json.loads(path.read_text())["swept"][slug])
    return entry


def _write_sweep(tmp_path: Path, swept: dict[str, Any]) -> None:
    path = tmp_path / "discovery" / "sweep-greenhouse.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"swept": swept}))


class TestDormancyStreak:
    """`empty_streak` counts consecutive probes that found zero postings."""

    def test_streak_increments_on_consecutive_empty_probes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = {"quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0)}
        _sweep(tmp_path, monkeypatch, ["quiet-co"], empty)
        assert _swept_entry(tmp_path, "quiet-co")["empty_streak"] == 1

        _age_sweep_stamps(tmp_path, 45)
        _sweep(tmp_path, monkeypatch, ["quiet-co"], empty)
        assert _swept_entry(tmp_path, "quiet-co")["empty_streak"] == 2

    def test_streak_resets_when_the_board_posts_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "wakes-co"
        empty = {slug: discovery.ProbeResult(slug, "swept", 0, total=0)}
        _sweep(tmp_path, monkeypatch, [slug], empty)
        _age_sweep_stamps(tmp_path, 45)
        _sweep(tmp_path, monkeypatch, [slug], empty)
        assert _swept_entry(tmp_path, slug)["empty_streak"] == 2

        # Two empties arms dormancy at the default threshold, so the board is
        # only due again after the stretched 180-day cadence.
        _age_sweep_stamps(tmp_path, 200)
        posting = {slug: discovery.ProbeResult(slug, "swept", 0, total=7)}
        _sweep(tmp_path, monkeypatch, [slug], posting)
        entry = _swept_entry(tmp_path, slug)
        assert entry["empty_streak"] == 0
        assert entry["total"] == 7

    def test_a_populated_board_never_starts_a_streak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total > 0 is alive-but-not-matching, the 83% that must keep cadence."""
        outcomes = {"busy-co": discovery.ProbeResult("busy-co", "swept", 0, total=40)}
        _sweep(tmp_path, monkeypatch, ["busy-co"], outcomes)
        assert _swept_entry(tmp_path, "busy-co")["empty_streak"] == 0


class TestDormancyBackoff:
    """A dormant board's re-probe cadence stretches by the multiplier."""

    def _dormant_ctx(self, **over: int) -> ScrapeContext:
        args: dict[str, int] = {
            "reprobe_days": 30,
            "dormant_after_empty_sweeps": 1,
            "dormant_reprobe_multiplier": 6,
        }
        args.update(over)
        return _ctx(**args)

    def _seed_dormant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ctx: ScrapeContext
    ) -> None:
        empty = {"quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0)}
        _sweep(tmp_path, monkeypatch, ["quiet-co"], empty, ctx=ctx)

    def test_dormant_board_is_not_reprobed_before_the_stretched_cutoff(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = self._dormant_ctx()
        self._seed_dormant(tmp_path, monkeypatch, ctx)
        _age_sweep_stamps(tmp_path, 45)  # past 30d, well short of 180d
        result = _sweep(tmp_path, monkeypatch, ["quiet-co"], {}, ctx=ctx)
        assert result.candidates == 0
        assert result.restaled == 0

    def test_dormant_board_is_reprobed_after_the_stretched_cutoff(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = self._dormant_ctx()
        self._seed_dormant(tmp_path, monkeypatch, ctx)
        _age_sweep_stamps(tmp_path, 200)  # past 180d
        empty = {"quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0)}
        result = _sweep(tmp_path, monkeypatch, ["quiet-co"], empty, ctx=ctx)
        assert result.restaled == 1
        assert _swept_entry(tmp_path, "quiet-co")["empty_streak"] == 2

    def test_full_sweep_reprobes_a_dormant_board(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--full is the escape hatch for a board wrongly judged dormant."""
        ctx = self._dormant_ctx()
        self._seed_dormant(tmp_path, monkeypatch, ctx)
        _age_sweep_stamps(tmp_path, 45)
        empty = {"quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0)}
        result = _sweep(tmp_path, monkeypatch, ["quiet-co"], empty, ctx=ctx, full=True)
        assert result.candidates == 1
        assert result.swept == 1

    def test_zero_threshold_disables_dormancy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A streak is always >= 0, so 0 must mean off, not "every board"."""
        ctx = self._dormant_ctx(dormant_after_empty_sweeps=0)
        self._seed_dormant(tmp_path, monkeypatch, ctx)
        _age_sweep_stamps(tmp_path, 45)
        empty = {"quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0)}
        result = _sweep(tmp_path, monkeypatch, ["quiet-co"], empty, ctx=ctx)
        assert result.restaled == 1

    def test_entry_without_total_never_arms_dormancy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pre-#251 entries have no total; they must not read as empty."""
        stamp = (now() - timedelta(days=200)).isoformat()
        _write_sweep(tmp_path, {"old-co": {"last_swept": stamp, "matched": 0}})
        ctx = self._dormant_ctx()
        empty = {"old-co": discovery.ProbeResult("old-co", "swept", 0, total=0)}
        result = _sweep(tmp_path, monkeypatch, ["old-co"], empty, ctx=ctx)
        assert result.restaled == 1
        assert _swept_entry(tmp_path, "old-co")["empty_streak"] == 1

    def test_dormant_boards_do_not_crowd_out_live_ones_at_the_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ranking is by how overdue a board is against its OWN cadence.

        Sorting on the raw stamp would always rank the dormant board first —
        it waits six times as long, so its stamp is necessarily older — and
        starve the boards that actually post.
        """
        dormant_stamp = (now() - timedelta(days=185)).isoformat()  # due 5d ago
        live_stamp = (now() - timedelta(days=40)).isoformat()  # due 10d ago
        _write_sweep(
            tmp_path,
            {
                "quiet-co": {
                    "last_swept": dormant_stamp,
                    "matched": 0,
                    "total": 0,
                    "empty_streak": 3,
                },
                "busy-co": {
                    "last_swept": live_stamp,
                    "matched": 2,
                    "total": 60,
                    "empty_streak": 0,
                },
            },
        )
        ctx = self._dormant_ctx(max_reprobe_per_sweep=1)
        outcomes = {
            "busy-co": discovery.ProbeResult("busy-co", "swept", 2, total=60),
            "quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0),
        }
        result = _sweep(
            tmp_path, monkeypatch, ["quiet-co", "busy-co"], outcomes, ctx=ctx
        )
        assert result.restaled == 1
        assert _swept_entry(tmp_path, "busy-co")["last_swept"] != live_stamp
        assert _swept_entry(tmp_path, "quiet-co")["last_swept"] == dormant_stamp


class TestDormancyConfig:
    def test_negative_threshold_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="dormant_after_empty_sweeps"):
            _ctx(dormant_after_empty_sweeps=-1)

    def test_zero_multiplier_is_rejected(self) -> None:
        """0 would mean "never re-probe" -- that is retirement, not dormancy."""
        with pytest.raises(ValidationError, match="dormant_reprobe_multiplier"):
            _ctx(dormant_reprobe_multiplier=0)

    def test_multiplier_of_one_is_a_legal_no_op(self) -> None:
        ctx = _ctx(dormant_reprobe_multiplier=1)
        assert ctx.plugin.discovery.dormant_reprobe_multiplier == 1

    def test_defaults(self) -> None:
        discovery_cfg = _ctx().plugin.discovery
        assert discovery_cfg.dormant_after_empty_sweeps == 2
        assert discovery_cfg.dormant_reprobe_multiplier == 6


class TestDormancyReporting:
    def test_sweep_reports_empty_and_dormant_totals(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _ctx(dormant_after_empty_sweeps=1)
        outcomes = {
            "quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0),
            "busy-co": discovery.ProbeResult("busy-co", "swept", 0, total=40),
            "match-co": discovery.ProbeResult("match-co", "swept", 3, total=12),
        }
        result = _sweep(
            tmp_path,
            monkeypatch,
            ["quiet-co", "busy-co", "match-co"],
            outcomes,
            ctx=ctx,
        )
        assert result.empty_total == 1
        assert result.dormant_total == 1
        assert result.matched_total == 1
        assert result.as_dict()["empty_total"] == 1

    def test_dormant_total_is_zero_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _ctx(dormant_after_empty_sweeps=0)
        outcomes = {"quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0)}
        result = _sweep(tmp_path, monkeypatch, ["quiet-co"], outcomes, ctx=ctx)
        assert result.empty_total == 1
        assert result.dormant_total == 0

    def test_empty_board_logs_differently_from_a_non_matching_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
    ) -> None:
        outcomes = {
            "quiet-co": discovery.ProbeResult("quiet-co", "swept", 0, total=0),
            "busy-co": discovery.ProbeResult("busy-co", "swept", 0, total=40),
        }
        with caplog.at_level("DEBUG"):
            _sweep(tmp_path, monkeypatch, ["quiet-co", "busy-co"], outcomes)
        assert "quiet-co: empty board" in caplog.text
        assert "busy-co: no matching titles (40 postings)" in caplog.text
