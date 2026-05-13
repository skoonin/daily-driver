"""Tests for the WeWorkRemotely RSS scraper source."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from daily_driver.scraper.sources import weworkremotely as wwr_module

_FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "scraper"
    / "weworkremotely"
    / "sample.xml"
)


def _config(
    roles: list[str] | None = None, categories: list[str] | None = None
) -> dict[str, Any]:
    return {
        "job_search": {
            "roles": roles if roles is not None else ["Engineer", "SRE"],
            "scraper": {
                "enabled": True,
                "timeout": 1,
                "max_retries": 0,
                "wwr_categories": (
                    categories if categories is not None else ["programming"]
                ),
            },
        }
    }


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
