"""Tests for individual scrapers and run_all_scrapers() orchestrator."""

import copy
from unittest.mock import MagicMock, patch

import pytest
import requests

import scrape_jobs as sj

from fixtures import SAMPLE_CONFIG


def _mock_response(json_data=None, content=None):
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    if json_data is not None:
        mock.json.return_value = json_data
    if content is not None:
        mock.content = content
    return mock


# ── RemoteOK ──────────────────────────────────────────────────────────────────

class TestScrapeRemoteOK:
    _api_data = [
        {"legal": "RemoteOK metadata"},  # always first, always skipped
        {"position": "Senior SRE", "company": "Acme", "url": "https://remoteok.io/1", "location": "Remote"},
        {"position": "Frontend Developer", "company": "Beta", "url": "https://remoteok.io/2", "location": ""},
        {"position": "Staff Platform Engineer", "company": "Gamma", "url": "https://remoteok.io/3", "location": "USA"},
    ]

    def test_returns_only_matching_jobs(self):
        with patch("scrape_jobs.requests.get", return_value=_mock_response(self._api_data)):
            jobs = sj.scrape_remoteok(SAMPLE_CONFIG)
        titles = [j["role"] for j in jobs]
        assert "Senior SRE" in titles
        assert "Staff Platform Engineer" in titles
        assert "Frontend Developer" not in titles

    def test_skips_metadata_element(self):
        data = [{"legal": "meta"}, {"position": "SRE", "company": "Co", "url": "https://x.com"}]
        with patch("scrape_jobs.requests.get", return_value=_mock_response(data)):
            jobs = sj.scrape_remoteok(SAMPLE_CONFIG)
        assert len(jobs) == 1

    def test_constructs_url_from_id_when_url_empty(self):
        data = [{"legal": "..."}, {"position": "SRE", "company": "Co", "id": "99999", "url": ""}]
        with patch("scrape_jobs.requests.get", return_value=_mock_response(data)):
            jobs = sj.scrape_remoteok(SAMPLE_CONFIG)
        assert "99999" in jobs[0]["url"]

    def test_defaults_empty_location_to_remote(self):
        data = [{"legal": "..."}, {"position": "SRE", "company": "Co", "url": "https://x.com", "location": ""}]
        with patch("scrape_jobs.requests.get", return_value=_mock_response(data)):
            jobs = sj.scrape_remoteok(SAMPLE_CONFIG)
        assert jobs[0]["location"] == "Remote"

    def test_source_label(self):
        data = [{"legal": "..."}, {"position": "SRE", "company": "Co", "url": "https://x.com"}]
        with patch("scrape_jobs.requests.get", return_value=_mock_response(data)):
            jobs = sj.scrape_remoteok(SAMPLE_CONFIG)
        assert jobs[0]["source"] == "RemoteOK"

    def test_skips_non_dict_items(self):
        data = [{"legal": "meta"}, "not-a-dict", {"position": "SRE", "company": "Co", "url": "https://x.com"}]
        with patch("scrape_jobs.requests.get", return_value=_mock_response(data)):
            jobs = sj.scrape_remoteok(SAMPLE_CONFIG)
        assert len(jobs) == 1

    def test_respects_max_jobs_limit(self):
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["job_search"]["scraper"]["remoteok_max_jobs"] = 1
        data = [
            {"legal": "meta"},
            {"position": "SRE", "company": "A", "url": "https://a.com"},
            {"position": "Staff SRE", "company": "B", "url": "https://b.com"},
        ]
        with patch("scrape_jobs.requests.get", return_value=_mock_response(data)):
            jobs = sj.scrape_remoteok(cfg)
        assert len(jobs) == 1


# ── WeWorkRemotely ────────────────────────────────────────────────────────────

class TestScrapeWeWorkRemotely:
    _rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <item>
    <title>Acme: Senior SRE</title>
    <guid>https://weworkremotely.com/jobs/1</guid>
    <region>Worldwide</region>
  </item>
  <item>
    <title>Beta: Junior Frontend Developer</title>
    <guid>https://weworkremotely.com/jobs/2</guid>
    <region>USA Only</region>
  </item>
  <item>
    <title>jobs</title>
    <guid>https://weworkremotely.com/</guid>
    <region></region>
  </item>
</channel>
</rss>"""

    def test_returns_only_matching_jobs(self):
        with patch("scrape_jobs.requests.get", return_value=_mock_response(content=self._rss)):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert len(jobs) == 1
        assert jobs[0]["role"] == "Senior SRE"

    def test_splits_company_and_role_on_colon(self):
        with patch("scrape_jobs.requests.get", return_value=_mock_response(content=self._rss)):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert jobs[0]["company"] == "Acme"
        assert jobs[0]["role"] == "Senior SRE"

    def test_extracts_region_as_location(self):
        with patch("scrape_jobs.requests.get", return_value=_mock_response(content=self._rss)):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert jobs[0]["location"] == "Worldwide"

    def test_source_label(self):
        with patch("scrape_jobs.requests.get", return_value=_mock_response(content=self._rss)):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert jobs[0]["source"] == "We Work Remotely"

    def test_skips_item_without_title_tag(self):
        rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><guid>https://weworkremotely.com/jobs/0</guid></item>
  <item><title>Acme: Senior SRE</title><guid>https://weworkremotely.com/jobs/1</guid></item>
</channel></rss>"""
        with patch("scrape_jobs.requests.get", return_value=_mock_response(content=rss)):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert len(jobs) == 1
        assert jobs[0]["role"] == "Senior SRE"

    def test_no_colon_title_uses_full_text_as_role(self):
        rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Senior SRE</title><guid>https://weworkremotely.com/jobs/1</guid></item>
</channel></rss>"""
        with patch("scrape_jobs.requests.get", return_value=_mock_response(content=rss)):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert len(jobs) == 1
        assert jobs[0]["company"] == ""
        assert jobs[0]["role"] == "Senior SRE"

    def test_falls_back_to_link_tag_when_no_guid(self):
        rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Acme: Senior SRE</title><link>https://weworkremotely.com/jobs/99</link></item>
</channel></rss>"""
        with patch("scrape_jobs.requests.get", return_value=_mock_response(content=rss)):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert jobs[0]["url"] == "https://weworkremotely.com/jobs/99"


# ── run_all_scrapers ──────────────────────────────────────────────────────────

class TestRunAllScrapers:
    _base_config = {
        "job_search": {
            "roles": ["SRE"],
            "scraper": {
                "enabled": True,
                "timeout": 5,
                "sources": {
                    "remoteok": False,
                    "weworkremotely": False,
                    "hn_who_is_hiring": False,
                    "anthropic": False,
                },
            },
        },
    }

    def _config_with(self, **sources):
        cfg = copy.deepcopy(self._base_config)
        cfg["job_search"]["scraper"]["sources"].update(sources)
        return cfg

    def test_runs_enabled_sources_only(self):
        cfg = self._config_with(remoteok=True)
        mock_ro = MagicMock(return_value=[{"url": "https://remoteok.io/1"}])
        mock_wwr = MagicMock(return_value=[])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            sj.run_all_scrapers(cfg)
        mock_ro.assert_called_once_with(cfg)
        mock_wwr.assert_not_called()

    def test_deduplicates_same_url_across_sources(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        dup_url = "https://shared.example.com/job"
        mock_ro = MagicMock(return_value=[{"url": dup_url}])
        mock_wwr = MagicMock(return_value=[{"url": dup_url}])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1

    def test_continues_after_timeout(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        mock_ro = MagicMock(side_effect=requests.exceptions.Timeout())
        mock_wwr = MagicMock(return_value=[{"url": "https://b.com"}])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1

    def test_continues_after_request_exception(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        mock_ro = MagicMock(side_effect=requests.exceptions.ConnectionError())
        mock_wwr = MagicMock(return_value=[{"url": "https://b.com"}])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1

    def test_continues_after_unexpected_exception(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        mock_ro = MagicMock(side_effect=RuntimeError("boom"))
        mock_wwr = MagicMock(return_value=[{"url": "https://b.com"}])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1

    def test_all_disabled_returns_empty(self):
        jobs = sj.run_all_scrapers(self._base_config)
        assert jobs == []
