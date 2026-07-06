"""Tests for the RemoteOK JSON API scraper source."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import remoteok as remoteok_module


def _config(
    roles: list[str] | None = None, tags: list[str] | None = None
) -> ScrapeContext:
    data: dict[str, Any] = {
        "roles": roles if roles is not None else ["Engineer", "SRE"],
        "scraper": {
            "enabled": True,
            "timeout": 1,
            "max_retries": 0,
        },
    }
    if tags is not None:
        data["sources"] = {"remoteok": {"remoteok_tags": tags}}
    return ScrapeContext(plugin=JobSearchPlugin.model_validate(data))


def _api_response(payload: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_float_string_salary_does_not_fail_source(monkeypatch: Any) -> None:
    """A row with a non-integer salary must not abort the whole source.

    RemoteOK can return floats or float-strings for salary. A bare int()
    coercion raises ValueError and (via the runner's broad except) drops
    every row from the source. The tolerant coercion must keep the clean
    rows and still surface the odd row with its comp formatted.
    """
    payload = [
        {
            "id": "1",
            "position": "Site Reliability Engineer",
            "company": "Acme",
            "url": "https://remoteok.com/1",
            "salary_min": "120000.5",
            "salary_max": "150000.9",
            "salary_currency": "USD",
        },
        {
            "id": "2",
            "position": "Staff Engineer",
            "company": "Globex",
            "url": "https://remoteok.com/2",
            "salary_min": 130000,
            "salary_max": 160000,
            "salary_currency": "USD",
        },
    ]
    monkeypatch.setattr(
        remoteok_module, "_api_get", lambda *a, **kw: _api_response(payload)
    )
    monkeypatch.setattr(remoteok_module, "_http_session", lambda cfg: MagicMock())

    jobs = remoteok_module.scrape_remoteok(_config())

    companies = {j["company"] for j in jobs}
    assert companies == {"Acme", "Globex"}

    by_company = {j["company"]: j for j in jobs}
    # Clean integer row formats normally.
    assert by_company["Globex"]["comp"] == "$130,000-$160,000/yr"
    # Float-string row is coerced (truncated via int(float(...))), not dropped.
    assert by_company["Acme"]["comp"] == "$120,000-$150,000/yr"


def test_queries_tag_endpoints_and_dedupes(monkeypatch: Any) -> None:
    """RemoteOK's unfiltered /api returns only the newest ~100 listings
    site-wide, where any one role is sparse (infra: live 0 of 100). The scraper
    must also query the configured tag endpoints (here devops/kubernetes/aws),
    which surface relevant roles directly, and dedupe by id across all
    endpoints."""
    by_url: dict[str, list[dict]] = {
        "https://remoteok.com/api": [
            {"id": "1", "position": "Marketing Manager", "company": "Noise"},
        ],
        "https://remoteok.com/api?tags=devops": [
            {"id": "2", "position": "Site Reliability Engineer", "company": "Acme"},
            {"id": "3", "position": "Staff DevOps Engineer", "company": "Globex"},
        ],
        "https://remoteok.com/api?tags=kubernetes": [
            # Same job id=2 as devops endpoint: must dedupe, not double-count.
            {"id": "2", "position": "Site Reliability Engineer", "company": "Acme"},
            {"id": "4", "position": "Senior Cloud Engineer", "company": "Initech"},
        ],
        "https://remoteok.com/api?tags=aws": [
            {"id": "5", "position": "Recruiter", "company": "Noise2"},
        ],
    }

    def fake_get(_session: Any, url: str, _ctx: Any, **_kw: Any) -> MagicMock:
        return _api_response(by_url.get(url, []))

    monkeypatch.setattr(remoteok_module, "_api_get", fake_get)
    monkeypatch.setattr(remoteok_module, "_http_session", lambda cfg: MagicMock())

    jobs = remoteok_module.scrape_remoteok(
        _config(
            roles=["site reliability engineer", "devops", "cloud engineer"],
            tags=["devops", "kubernetes", "aws"],
        )
    )

    companies = {j["company"] for j in jobs}
    # Infra roles from the tag endpoints land; the non-matching newest-100 noise
    # does not; the cross-endpoint duplicate (id=2) appears once.
    assert companies == {"Acme", "Globex", "Initech"}
    assert sum(1 for j in jobs if j["company"] == "Acme") == 1


def test_tags_drive_endpoints_no_hardcoded_defaults(monkeypatch: Any) -> None:
    """The ?tags= slugs come from config, not a baked-in infra set.

    With no configured tags only the unfiltered feed is queried; configured
    slugs each add one ?tags= view. A non-infra search never hits the old
    devops/kubernetes/aws endpoints.
    """
    calls: list[str] = []

    def fake_get(_session: Any, url: str, _ctx: Any, **_kw: Any) -> MagicMock:
        calls.append(url)
        return _api_response([])

    monkeypatch.setattr(remoteok_module, "_api_get", fake_get)
    monkeypatch.setattr(remoteok_module, "_http_session", lambda cfg: MagicMock())

    remoteok_module.scrape_remoteok(_config())
    assert calls == ["https://remoteok.com/api"]

    calls.clear()
    remoteok_module.scrape_remoteok(_config(tags=["hr", "marketing"]))
    assert calls == [
        "https://remoteok.com/api",
        "https://remoteok.com/api?tags=hr",
        "https://remoteok.com/api?tags=marketing",
    ]


def test_description_html_is_captured_and_stripped(monkeypatch: Any) -> None:
    """The API's HTML ``description`` is mapped to ``description_text`` as plain
    text so it feeds enrichment/scoring; a row without it stays empty."""
    payload = [
        {
            "id": "1",
            "position": "Site Reliability Engineer",
            "company": "Acme",
            "url": "https://remoteok.com/1",
            "description": "<p>Run <b>Kubernetes</b> at scale.</p>",
        },
        {
            "id": "2",
            "position": "Staff Engineer",
            "company": "Globex",
            "url": "https://remoteok.com/2",
        },
    ]
    monkeypatch.setattr(
        remoteok_module, "_api_get", lambda *a, **kw: _api_response(payload)
    )
    monkeypatch.setattr(remoteok_module, "_http_session", lambda cfg: MagicMock())

    by_company = {j["company"]: j for j in remoteok_module.scrape_remoteok(_config())}
    assert by_company["Acme"]["description_text"] == "Run Kubernetes at scale."
    assert by_company["Globex"]["description_text"] == ""


def test_stop_event_skips_remaining_tag_fetches(monkeypatch: Any) -> None:
    """A stop request mid-scrape must stop issuing further endpoint fetches and
    keep what was already collected."""
    calls: list[str] = []

    def fake_get(_session: Any, url: str, ctx: Any, **_kw: Any) -> MagicMock:
        calls.append(url)
        ctx.stop_event.set()  # trip after the first endpoint returns
        return _api_response(
            [{"id": "1", "position": "Site Reliability Engineer", "company": "Acme"}]
        )

    monkeypatch.setattr(remoteok_module, "_api_get", fake_get)
    monkeypatch.setattr(remoteok_module, "_http_session", lambda cfg: MagicMock())

    jobs = remoteok_module.scrape_remoteok(
        _config(
            roles=["site reliability engineer"], tags=["devops", "kubernetes", "aws"]
        )
    )

    assert len(calls) == 1
    assert [j["company"] for j in jobs] == ["Acme"]
