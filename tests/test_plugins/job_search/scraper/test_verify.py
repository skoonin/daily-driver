"""`jobs verify` url-check liveness verification (lifecycle plan phase 4).

Guards under test: only stale untriaged url-check rows are selected, each
source's closed detector reads the live-probed signal (2026-07-04) and nothing
else, `unknown` never closes, and the detector-rot circuit breaker discards a
source's closures when 100% of a real sample reads closed.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import verify
from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
from daily_driver.plugins.job_search.scraper.sources import (
    SCRAPERS,
    SOURCE_CAPABILITIES,
)

_TODAY = dt.date(2026, 7, 4)
_TODAY_ISO = _TODAY.isoformat()
_STALE = "2026-06-01"
_FRESH = "2026-07-03"


def _plugin() -> JobSearchPlugin:
    return JobSearchPlugin.model_validate({"scraper": {"enabled": True}})


def _row(url: str, source: str, status: str = "found", **cells: str) -> dict[str, str]:
    base = {column: "" for column in CANONICAL_HEADER}
    base.update(
        {
            "Status": status,
            "Company": "Acme",
            "Role": "SRE",
            "Link": url,
            "Date Found": _STALE,
            "Source": source,
        }
    )
    base.update(cells)
    return base


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class _FakeResp:
    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        url: str = "",
        json_body: Any = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self._json_body = json_body

    def json(self) -> Any:
        if self._json_body is None:
            raise ValueError("no JSON")
        return self._json_body


def _patch_responses(monkeypatch, responses: dict[str, Any]) -> list[str]:
    """Route verify's HTTP by URL; record the request order."""
    requested: list[str] = []

    def _fake_api_request(session, method, url, ctx, **kwargs):
        requested.append(url)
        return responses.get(url)

    monkeypatch.setattr(verify, "_api_request", _fake_api_request)
    return requested


# ── Source-cell mapping (registry sync) ──────────────────────────────────────


def test_every_scraper_source_cell_maps_to_its_capabilities_key() -> None:
    """Each adapter's emitted Source string must resolve to its SCRAPERS id,
    otherwise verify silently skips that source forever."""
    emitted = {
        "apple": "Apple Careers",
        "hn_jobs": "HN Jobs",
        "hn_who_is_hiring": "HN Who's Hiring",
        "remoteok": "RemoteOK",
        "weworkremotely": "We Work Remotely",
        "linkedin": "linkedin",
        "indeed": "indeed",
        "greenhouse": "Greenhouse (acme)",
        "ashby": "Ashby (acme)",
        "lever": "Lever (acme)",
        "workable": "Workable (acme)",
        "workday": "Workday (acme)",
    }
    assert set(emitted) == set(
        SCRAPERS
    ), "adapter registry changed; extend this map and verify._SOURCE_CELL_IDS"
    for source_id, cell in emitted.items():
        assert verify.source_id_for_cell(cell) == source_id
        assert source_id in SOURCE_CAPABILITIES


def test_unknown_source_cell_maps_to_none() -> None:
    assert verify.source_id_for_cell("Mystery Board") is None
    assert verify.source_id_for_cell("") is None


# ── Selection ────────────────────────────────────────────────────────────────


def _select(rows, **kwargs):
    defaults = {"today": _TODAY, "reverify_days": 7, "source_filter": None}
    defaults.update(kwargs)
    return verify._select_targets(rows, **defaults)


def test_selects_only_stale_untriaged_url_check_rows() -> None:
    rows = [
        _row("https://l/1", "linkedin"),  # stale, in
        _row("https://l/2", "linkedin", **{"Date Verified": _FRESH}),  # fresh
        _row("https://l/3", "linkedin", status="applied"),  # triaged
        _row("https://l/4", "linkedin", **{"Date Closed": _STALE}),  # closed
        _row("https://g/1", "Greenhouse (acme)"),  # board-diff covers
        _row("https://i/1", "indeed"),  # verify: none
        _row("", "linkedin"),  # no URL
    ]
    targets = _select(rows)
    assert [t.url for t in targets] == ["https://l/1"]
    assert targets[0].source_id == "linkedin"


def test_missing_anchor_means_never_verified_and_always_due() -> None:
    rows = [_row("https://l/1", "linkedin", **{"Date Found": ""})]
    assert [t.url for t in _select(rows)] == ["https://l/1"]


def test_selection_orders_stalest_first_with_never_verified_leading() -> None:
    rows = [
        _row("https://l/mid", "linkedin", **{"Date Verified": "2026-06-15"}),
        _row("https://l/old", "linkedin", **{"Date Verified": "2026-05-01"}),
        _row("https://l/never", "linkedin", **{"Date Found": ""}),
    ]
    assert [t.url for t in _select(rows)] == [
        "https://l/never",
        "https://l/old",
        "https://l/mid",
    ]


def test_source_filter_narrows_selection() -> None:
    rows = [
        _row("https://l/1", "linkedin"),
        _row("https://r/1", "RemoteOK"),
    ]
    targets = _select(rows, source_filter=frozenset({"remoteok"}))
    assert [t.url for t in targets] == ["https://r/1"]


# ── Detectors ────────────────────────────────────────────────────────────────


def _ctx():
    from daily_driver.plugins.job_search.scraper.context import ScrapeContext

    return ScrapeContext(plugin=_plugin())


def test_linkedin_detector(monkeypatch) -> None:
    cases = {
        "https://li/404": (_FakeResp(404), ("closed", "url-404")),
        "https://li/live": (
            _FakeResp(200, text="<html>Apply now</html>", url="https://li/live"),
            ("live", ""),
        ),
        "https://li/marker": (
            _FakeResp(
                200, text="No Longer Accepting Applications", url="https://li/marker"
            ),
            ("closed", "url-marker"),
        ),
        "https://li/wall": (
            _FakeResp(200, text="", url="https://www.linkedin.com/authwall?x=1"),
            ("unknown", "auth wall"),
        ),
        "https://li/999": (_FakeResp(999), ("unknown", "http 999")),
        "https://li/dead": (None, ("unknown", "request failed")),
    }
    _patch_responses(monkeypatch, {url: resp for url, (resp, _v) in cases.items()})
    for url, (_resp, expected) in cases.items():
        outcome = verify._check_linkedin(url, None, _ctx())
        assert (outcome.verdict, outcome.reason) == expected, url


def test_apple_detector_uses_json_api_never_html(monkeypatch) -> None:
    api = "https://jobs.apple.com/api/v1/jobDetails/{}?locale=en-us"
    responses = {
        api.format("111"): _FakeResp(200, json_body={"res": {"id": "PIPE-111"}}),
        api.format("222"): _FakeResp(
            404, json_body={"error": "jobsite.general.serviceError"}
        ),
        api.format("333"): _FakeResp(404, text="<html>not found</html>"),
    }
    requested = _patch_responses(monkeypatch, responses)

    live = verify._check_apple(
        "https://jobs.apple.com/en-us/details/111/some-role", None, _ctx()
    )
    assert live.verdict == "live"
    closed = verify._check_apple(
        "https://jobs.apple.com/en-us/details/222/some-role", None, _ctx()
    )
    assert (closed.verdict, closed.reason) == ("closed", "url-404")
    # An HTML 404 means the API endpoint moved, not that the role died.
    moved = verify._check_apple(
        "https://jobs.apple.com/en-us/details/333/some-role", None, _ctx()
    )
    assert moved.verdict == "unknown"
    no_id = verify._check_apple("https://jobs.apple.com/en-us/search", None, _ctx())
    assert no_id.verdict == "unknown"
    assert all("/api/v1/jobDetails/" in url for url in requested)


def test_weworkremotely_detector_reads_redirects(monkeypatch) -> None:
    live_url = "https://weworkremotely.com/remote-jobs/acme-sre"
    dead_url = "https://weworkremotely.com/remote-jobs/gone-sre"
    responses = {
        live_url: _FakeResp(200, url=live_url),
        dead_url: _FakeResp(200, url="https://weworkremotely.com/"),
    }
    _patch_responses(monkeypatch, responses)
    assert verify._check_weworkremotely(live_url, None, _ctx()).verdict == "live"
    dead = verify._check_weworkremotely(dead_url, None, _ctx())
    assert (dead.verdict, dead.reason) == ("closed", "url-marker")


def test_remoteok_detector_marker_and_id_guard(monkeypatch) -> None:
    live_url = "https://remoteOK.com/remote-jobs/remote-sre-acme-1134015"
    closed_url = "https://remoteok.com/remote-jobs/remote-old-role-999"
    hijack_url = "https://remoteok.com/remote-jobs/remote-x-123"
    responses = {
        live_url: _FakeResp(
            200,
            text="<h2>SRE</h2>",
            url="https://remoteok.com/remote-jobs/1134015-remote-sre-acme",
        ),
        closed_url: _FakeResp(
            200,
            text="This job post is CLOSED and the position is probably filled.",
            url="https://remoteok.com/remote-jobs/999-remote-old-role",
        ),
        hijack_url: _FakeResp(
            200, text="", url="https://remoteok.com/remote-jobs/456-other-listing"
        ),
    }
    _patch_responses(monkeypatch, responses)
    assert verify._check_remoteok(live_url, None, _ctx()).verdict == "live"
    closed = verify._check_remoteok(closed_url, None, _ctx())
    assert (closed.verdict, closed.reason) == ("closed", "url-marker")
    # Redirected to a listing that lost our id: no verdict about our row.
    assert verify._check_remoteok(hijack_url, None, _ctx()).verdict == "unknown"


def test_hn_detector_skips_permalinks(monkeypatch) -> None:
    permalink = "https://news.ycombinator.com/item?id=1234"
    external = "https://acme.example/careers/sre"
    responses = {external: _FakeResp(404)}
    requested = _patch_responses(monkeypatch, responses)
    skipped = verify._check_hn(permalink, None, _ctx())
    assert skipped.verdict == "unknown"
    closed = verify._check_hn(external, None, _ctx())
    assert (closed.verdict, closed.reason) == ("closed", "url-404")
    assert requested == [external]  # the permalink was never fetched


def test_every_url_check_source_has_a_checker() -> None:
    """A url-check capability without a registered checker is a KeyError at
    probe time (hn_who_is_hiring nearly shipped that way)."""
    url_check_sources = {
        source_id
        for source_id, capability in SOURCE_CAPABILITIES.items()
        if capability.verify == "url-check"
    }
    assert url_check_sources == set(verify._URL_CHECKERS)


# ── Age-unverified fallback (verify="none" sources + HN permalink rows) ──────


def test_old_unverifiable_rows_close_as_age_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [
        _row("https://indeed.com/viewjob?jk=1", "indeed", Notes="good fit"),
        _row("https://news.ycombinator.com/item?id=2", "HN Who's Hiring"),
        _row("https://indeed.com/viewjob?jk=3", "indeed", **{"Date Found": _FRESH}),
    ]
    csv_path, report = _run_verify(tmp_path, rows, {}, monkeypatch)

    assert report.checked == {}  # the age channel probes nothing
    assert len(report.closed) == 2
    assert {entry["reason"] for entry in report.closed} == {"age-unverified"}
    assert {entry["source"] for entry in report.closed} == {
        "indeed",
        "hn_who_is_hiring",
    }

    by_url = {row["Link"]: row for row in _read_csv(csv_path)}
    aged = by_url["https://indeed.com/viewjob?jk=1"]
    assert aged["Status"] == "closed"
    assert aged["Date Closed"] == _TODAY_ISO
    assert aged["Notes"] == f"good fit | [closed: age-unverified {_TODAY_ISO}]"
    fresh = by_url["https://indeed.com/viewjob?jk=3"]
    assert fresh["Status"] == "found"
    assert fresh["Date Closed"] == ""

    manifest = json.loads((tmp_path / "jobs-last-verify.json").read_text())
    assert manifest["unverified_age_days"] == 30
    assert len(manifest["closed"]) == 2


def test_hn_jobs_permalink_rows_age_close_but_external_urls_stay_url_checked(
    tmp_path: Path, monkeypatch
) -> None:
    """The permalink fallback rows belong to the age channel; hn_jobs rows
    with a real external URL keep the stronger url-check evidence."""
    permalink = "https://news.ycombinator.com/item?id=9"
    external = "https://acme.example/careers/sre"
    rows = [
        _row(permalink, "HN Jobs"),
        _row(external, "HN Jobs"),
    ]
    responses = {external: _FakeResp(200, url=external)}
    csv_path, report = _run_verify(tmp_path, rows, responses, monkeypatch)

    assert report.checked == {"hn_jobs": 1}  # only the external URL was probed
    assert [entry["url"] for entry in report.closed] == [permalink]
    assert report.closed[0]["reason"] == "age-unverified"
    by_url = {row["Link"]: row for row in _read_csv(csv_path)}
    assert by_url[permalink]["Status"] == "closed"
    assert by_url[external]["Date Verified"] == _TODAY_ISO


def test_recently_resighted_row_is_never_age_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """Scrape re-sightings refresh Date Verified for every source (including
    verify="none" ones); a job the pipeline saw live days ago must not close
    off its old discovery date."""
    rows = [
        _row(
            "https://indeed.com/viewjob?jk=1",
            "indeed",
            **{"Date Found": "2026-05-01", "Date Verified": _FRESH},
        )
    ]
    csv_path, report = _run_verify(tmp_path, rows, {}, monkeypatch)
    assert report.closed == []
    assert _read_csv(csv_path)[0]["Status"] == "found"


def test_row_with_no_parseable_dates_is_never_age_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """No anchor = no closure. Opposite of the probe path's always-due rule,
    because here selection IS the closure decision."""
    rows = [
        _row(
            "https://indeed.com/viewjob?jk=1",
            "indeed",
            **{"Date Found": "", "Date Verified": ""},
        )
    ]
    csv_path, report = _run_verify(tmp_path, rows, {}, monkeypatch)
    assert report.closed == []
    assert _read_csv(csv_path)[0]["Status"] == "found"


def test_age_threshold_from_config_with_flag_override(
    tmp_path: Path, monkeypatch
) -> None:
    plugin = JobSearchPlugin.model_validate(
        {"scraper": {"enabled": True}, "verify": {"unverified_age_days": 60}}
    )
    csv_path = tmp_path / "jobs.csv"
    # Found 33 days ago: not due under the configured 60...
    _write_csv(csv_path, [_row("https://indeed.com/viewjob?jk=1", "indeed")])
    _patch_responses(monkeypatch, {})
    report = verify.verify_jobs(
        csv_path, tmp_path, plugin, today=_TODAY, sleep=lambda _s: None
    )
    assert report.closed == []
    # ...but the per-run flag overrides the config.
    report = verify.verify_jobs(
        csv_path,
        tmp_path,
        plugin,
        unverified_age_days=10,
        today=_TODAY,
        sleep=lambda _s: None,
    )
    assert [entry["reason"] for entry in report.closed] == ["age-unverified"]


def test_age_closure_respects_dry_run_and_triage(tmp_path: Path, monkeypatch) -> None:
    rows = [
        _row("https://indeed.com/viewjob?jk=1", "indeed"),
        _row("https://indeed.com/viewjob?jk=2", "indeed", status="applied"),
        _row("https://indeed.com/viewjob?jk=3", "indeed", **{"Date Closed": _STALE}),
    ]
    csv_path, report = _run_verify(tmp_path, rows, {}, monkeypatch, dry_run=True)
    # Only the untriaged, not-yet-closed row is a candidate; dry-run writes nothing.
    assert [entry["url"] for entry in report.closed] == [
        "https://indeed.com/viewjob?jk=1"
    ]
    assert _read_csv(csv_path)[0]["Status"] == "found"
    assert not (tmp_path / "jobs-last-verify.json").exists()


# ── verify_jobs end-to-end ───────────────────────────────────────────────────


def _run_verify(tmp_path: Path, rows, responses, monkeypatch, **kwargs):
    csv_path = tmp_path / "jobs.csv"
    _write_csv(csv_path, rows)
    _patch_responses(monkeypatch, responses)
    report = verify.verify_jobs(
        csv_path,
        tmp_path,
        _plugin(),
        today=_TODAY,
        sleep=lambda _s: None,
        **kwargs,
    )
    return csv_path, report


def test_verify_stamps_live_closed_and_leaves_unknown_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [
        _row("https://li/live", "linkedin", Notes="keep me"),
        _row("https://li/gone", "linkedin", Notes="great fit"),
        _row("https://li/wall", "linkedin"),
    ]
    responses = {
        "https://li/live": _FakeResp(200, text="Apply", url="https://li/live"),
        "https://li/gone": _FakeResp(404),
        "https://li/wall": None,
    }
    csv_path, report = _run_verify(tmp_path, rows, responses, monkeypatch)

    assert report.checked == {"linkedin": 3}
    assert report.live == 1
    assert [entry["url"] for entry in report.closed] == ["https://li/gone"]
    assert report.unknown == {"request failed": 1}

    by_url = {row["Link"]: row for row in _read_csv(csv_path)}
    assert by_url["https://li/live"]["Date Verified"] == _TODAY_ISO
    assert by_url["https://li/live"]["Status"] == "found"
    gone = by_url["https://li/gone"]
    assert gone["Status"] == "closed"
    assert gone["Date Closed"] == _TODAY_ISO
    assert gone["Notes"] == f"great fit | [closed: url-404 {_TODAY_ISO}]"
    wall = by_url["https://li/wall"]
    assert wall["Date Verified"] == ""
    assert wall["Status"] == "found"

    manifest = json.loads((tmp_path / "jobs-last-verify.json").read_text())
    assert manifest["checked"] == {"linkedin": 3}
    assert manifest["live"] == 1
    assert len(manifest["closed"]) == 1
    # Self-describing: the knobs that shaped the run are in the record.
    assert manifest["reverify_days"] == 7
    assert manifest["limit"] is None
    assert manifest["sources"] is None


def test_dry_run_probes_but_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    rows = [_row("https://li/gone", "linkedin")]
    responses = {"https://li/gone": _FakeResp(404)}
    csv_path, report = _run_verify(tmp_path, rows, responses, monkeypatch, dry_run=True)
    assert len(report.closed) == 1
    assert _read_csv(csv_path)[0]["Status"] == "found"
    assert not (tmp_path / "jobs-last-verify.json").exists()


def test_limit_checks_stalest_first(tmp_path: Path, monkeypatch) -> None:
    rows = [
        _row("https://li/fresher", "linkedin", **{"Date Verified": "2026-06-15"}),
        _row("https://li/stalest", "linkedin", **{"Date Verified": "2026-05-01"}),
    ]
    responses = {
        "https://li/stalest": _FakeResp(200, text="", url="https://li/stalest"),
    }
    csv_path, report = _run_verify(tmp_path, rows, responses, monkeypatch, limit=1)
    assert report.candidates == 2
    assert sum(report.checked.values()) == 1
    by_url = {row["Link"]: row for row in _read_csv(csv_path)}
    assert by_url["https://li/stalest"]["Date Verified"] == _TODAY_ISO
    assert by_url["https://li/fresher"]["Date Verified"] == "2026-06-15"


def test_all_closed_source_is_suspect_and_closes_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """100% closed across a real sample = the site changed shape, not a mass
    die-off; closures are discarded and the source reported."""
    rows = [_row(f"https://li/{n}", "linkedin") for n in range(10)]
    responses = {f"https://li/{n}": _FakeResp(404) for n in range(10)}
    csv_path, report = _run_verify(tmp_path, rows, responses, monkeypatch)

    assert report.suspect_sources == ["linkedin"]
    assert report.closed == []
    assert report.unknown == {"suspect source": 10}
    # The discards are preserved so "check the site by hand" has a row list.
    assert len(report.discarded_closures) == 10
    assert {entry["source"] for entry in report.discarded_closures} == {"linkedin"}
    assert all(row["Status"] == "found" for row in _read_csv(csv_path))

    manifest = json.loads((tmp_path / "jobs-last-verify.json").read_text())
    assert len(manifest["discarded_closures"]) == 10
    assert manifest["suspect_sources"] == ["linkedin"]


def test_below_suspect_threshold_all_closed_still_applies(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [_row(f"https://li/{n}", "linkedin") for n in range(3)]
    responses = {f"https://li/{n}": _FakeResp(404) for n in range(3)}
    csv_path, report = _run_verify(tmp_path, rows, responses, monkeypatch)
    assert report.suspect_sources == []
    assert len(report.closed) == 3
    assert all(row["Status"] == "closed" for row in _read_csv(csv_path))


def test_config_reverify_days_used_when_flag_absent(
    tmp_path: Path, monkeypatch
) -> None:
    plugin = JobSearchPlugin.model_validate(
        {"scraper": {"enabled": True}, "verify": {"reverify_days": 60}}
    )
    csv_path = tmp_path / "jobs.csv"
    # Verified 33 days ago: due under the default 7, NOT due under 60.
    _write_csv(
        csv_path, [_row("https://li/1", "linkedin", **{"Date Verified": _STALE})]
    )
    _patch_responses(monkeypatch, {})
    report = verify.verify_jobs(
        csv_path, tmp_path, plugin, today=_TODAY, sleep=lambda _s: None
    )
    assert report.candidates == 0
