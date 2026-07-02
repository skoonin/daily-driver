"""Tests for the WeWorkRemotely RSS scraper source."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import weworkremotely as wwr_module

_FIXTURE = (
    Path(__file__).parents[3] / "fixtures" / "scraper" / "weworkremotely" / "sample.xml"
)


def _config(
    roles: list[str] | None = None, categories: list[str] | None = None
) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": roles if roles is not None else ["Engineer", "SRE"],
                "scraper": {
                    "enabled": True,
                    "timeout": 1,
                    "max_retries": 0,
                },
                "sources": {
                    "weworkremotely": {
                        "wwr_categories": (
                            categories if categories is not None else ["programming"]
                        ),
                    },
                },
            }
        )
    )


def _rss_response() -> MagicMock:
    resp = MagicMock()
    resp.content = _FIXTURE.read_bytes()
    return resp


def test_parses_sample_listing(monkeypatch: Any) -> None:
    monkeypatch.setattr(wwr_module, "_api_get", lambda *a, **kw: _rss_response())
    monkeypatch.setattr(wwr_module, "_http_session", lambda cfg: MagicMock())

    jobs = wwr_module.scrape_weworkremotely(_config())

    assert len(jobs) >= 2
    companies = {j["company"] for j in jobs}
    assert "Acme Corp" in companies
    assert "Globex" in companies

    titles = {j["role"] for j in jobs}
    assert "Senior Software Engineer" in titles
    assert "Staff Engineer" in titles

    for job in jobs:
        assert job["source"] == "We Work Remotely"
        assert job["url"].startswith("https://weworkremotely.com/")


def test_description_html_is_captured_and_stripped(monkeypatch: Any) -> None:
    """The RSS <description> is mapped to ``description_text`` as plain text; an
    item with no <description> element stays empty."""
    monkeypatch.setattr(wwr_module, "_api_get", lambda *a, **kw: _rss_response())
    monkeypatch.setattr(wwr_module, "_http_session", lambda cfg: MagicMock())

    by_company = {j["company"]: j for j in wwr_module.scrape_weworkremotely(_config())}
    assert by_company["Acme Corp"]["description_text"] == "Build reliable systems."
    assert by_company["Globex"]["description_text"] == ""


def test_handles_empty_results(monkeypatch: Any) -> None:
    empty_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Empty</title></channel></rss>"""

    resp = MagicMock()
    resp.content = empty_rss
    monkeypatch.setattr(wwr_module, "_api_get", lambda *a, **kw: resp)
    monkeypatch.setattr(wwr_module, "_http_session", lambda cfg: MagicMock())

    jobs = wwr_module.scrape_weworkremotely(_config())
    assert jobs == []


def test_skips_when_no_categories_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(wwr_module, "_api_get", lambda *a, **kw: _rss_response())
    monkeypatch.setattr(wwr_module, "_http_session", lambda cfg: MagicMock())

    jobs = wwr_module.scrape_weworkremotely(_config(categories=[]))
    assert jobs == []


def test_wwr_categories_drive_fetched_rss_urls(monkeypatch: Any) -> None:
    """Each configured wwr_category becomes a /categories/remote-<cat>-jobs.rss fetch."""
    fetched: list[str] = []

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        fetched.append(url)
        return _rss_response()

    monkeypatch.setattr(wwr_module, "_api_get", fake_api_get)
    monkeypatch.setattr(wwr_module, "_http_session", lambda cfg: MagicMock())

    wwr_module.scrape_weworkremotely(_config(categories=["devops-sysadmin", "design"]))

    assert (
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"
        in fetched
    )
    assert "https://weworkremotely.com/categories/remote-design-jobs.rss" in fetched


def test_skips_malformed_rows(monkeypatch: Any) -> None:
    """Items with no role after colon-split are dropped."""
    malformed_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title><![CDATA[Company: ]]></title>
      <link>https://weworkremotely.com/bad-item</link>
      <region><![CDATA[Remote]]></region>
    </item>
    <item>
      <title><![CDATA[Good Corp: SRE]]></title>
      <link>https://weworkremotely.com/good-item</link>
      <region><![CDATA[Worldwide]]></region>
    </item>
  </channel>
</rss>"""

    resp = MagicMock()
    resp.content = malformed_rss
    monkeypatch.setattr(wwr_module, "_api_get", lambda *a, **kw: resp)
    monkeypatch.setattr(wwr_module, "_http_session", lambda cfg: MagicMock())

    jobs = wwr_module.scrape_weworkremotely(_config())
    assert all(j["role"] for j in jobs), "every returned job must have a non-empty role"
