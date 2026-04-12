"""Tests for scrape_greenhouse() — the Greenhouse Job Board API scraper."""

from unittest.mock import MagicMock, patch

import scrape_jobs as sj

from fixtures import SAMPLE_CONFIG


def _greenhouse_config(boards=None):
    """Return config with greenhouse_boards set."""
    cfg = dict(SAMPLE_CONFIG)
    cfg["job_search"] = dict(cfg.get("job_search", {}))
    cfg["job_search"]["scraper"] = dict(cfg["job_search"].get("scraper", {}))
    if boards is not None:
        cfg["job_search"]["scraper"]["greenhouse_boards"] = boards
    return cfg


_API_RESPONSE = {
    "jobs": [
        {
            "title": "Staff SRE",
            "location": {"name": "Remote"},
            "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/100001",
            "content": "<p>Build <b>distributed</b> infra.</p>",
            "company_name": "Anthropic",
        },
        {
            "title": "Junior Frontend Engineer",
            "location": {"name": "San Francisco, CA"},
            "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/100002",
            "content": "<p>React stuff.</p>",
            "company_name": "Anthropic",
        },
        {
            "title": "Senior Platform Engineer",
            "location": {"name": ""},
            "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/100003",
            "content": "<p>K8s platform.</p>",
            "company_name": "Anthropic",
        },
    ]
}


class TestScrapeGreenhouse:
    def _run(self, api_response=None, boards=None):
        if api_response is None:
            api_response = _API_RESPONSE
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = api_response
        cfg = _greenhouse_config(boards)
        with patch("requests.get", return_value=resp):
            return sj.scrape_greenhouse(cfg)

    def test_returns_matching_jobs(self):
        jobs = self._run()
        roles = [j["role"] for j in jobs]
        assert "Staff SRE" in roles

    def test_filters_non_matching_roles(self):
        jobs = self._run()
        roles = [j["role"] for j in jobs]
        assert "Junior Frontend Engineer" not in roles

    def test_company_from_api(self):
        jobs = self._run()
        assert all(j["company"] == "Anthropic" for j in jobs)

    def test_source_label(self):
        jobs = self._run()
        assert all(j["source"] == "Greenhouse (anthropic)" for j in jobs)

    def test_location_extracted(self):
        jobs = self._run()
        sre_job = next(j for j in jobs if j["role"] == "Staff SRE")
        assert sre_job["location"] == "Remote"

    def test_defaults_empty_location_to_remote(self):
        jobs = self._run()
        platform = next(j for j in jobs if "Platform" in j["role"])
        assert platform["location"] == "Remote"

    def test_description_text_extracted(self):
        jobs = self._run()
        sre_job = next(j for j in jobs if j["role"] == "Staff SRE")
        assert sre_job["description_text"] == "Build distributed infra."

    def test_returns_empty_when_no_matching_jobs(self):
        jobs = self._run({"jobs": []})
        assert jobs == []

    def test_returns_empty_on_api_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.RequestException("timeout")):
            jobs = sj.scrape_greenhouse(_greenhouse_config())
        assert jobs == []

    def test_multiple_boards(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = _API_RESPONSE
        cfg = _greenhouse_config(boards=["anthropic", "stripe"])
        with patch("requests.get", return_value=resp) as mock_get:
            sj.scrape_greenhouse(cfg)
        urls_called = [call[0][0] for call in mock_get.call_args_list]
        assert any("anthropic" in u for u in urls_called)
        assert any("stripe" in u for u in urls_called)

    def test_absolute_url_preserved(self):
        jobs = self._run()
        sre_job = next(j for j in jobs if j["role"] == "Staff SRE")
        assert sre_job["url"] == "https://boards.greenhouse.io/anthropic/jobs/100001"
