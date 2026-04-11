"""Tests for Playwright-based scrapers and run_all_scrapers() orchestrator."""

import copy
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
import requests

import scrape_jobs as sj

from fixtures import SAMPLE_CONFIG


# ── Playwright mock helpers ───────────────────────────────────────────────────

def _playwright_ctx_mock(page):
    """Replace _playwright_browser with a context manager yielding page."""
    @contextmanager
    def _impl(*args, **kwargs):
        yield page
    return _impl


def _mock_page(*element_lists):
    """Return a mock Playwright page whose query_selector_all returns element_lists in order."""
    page = MagicMock()
    page.goto.return_value = None
    page.wait_for_timeout.return_value = None
    # Each call to query_selector_all returns the next list in element_lists,
    # cycling back to the last one when exhausted.
    call_idx = [0]
    def _qsa(selector):
        idx = min(call_idx[0], len(element_lists) - 1)
        call_idx[0] += 1
        return element_lists[idx]
    page.query_selector_all.side_effect = _qsa
    return page


def _mock_element(inner_text="", *, href="", data_id="", extra_qs=None):
    """Return a mock DOM element with common Playwright methods."""
    el = MagicMock()
    el.inner_text.return_value = inner_text
    # get_attribute("href") → href, get_attribute("data-id") → data_id
    def _get_attr(name):
        if name in ("href",):
            return href
        if name in ("data-id", "id"):
            return data_id
        return None
    el.get_attribute.side_effect = _get_attr
    # query_selector on sub-elements
    def _qs(selector):
        if extra_qs:
            return extra_qs.get(selector)
        return None
    el.query_selector.side_effect = _qs
    # query_selector_all on sub-elements (e.g. for Apple row cells)
    el.query_selector_all.return_value = []
    return el


def _mock_row_el(text, href):
    """Minimal element with inner_text and get_attribute('href')."""
    el = MagicMock()
    el.inner_text.return_value = text
    el.get_attribute.return_value = href
    return el


# ── RemoteOK ──────────────────────────────────────────────────────────────────

def _remoteok_row(role, company, job_id, location="Remote"):
    """Build a mock tr.job row for the RemoteOK scraper."""
    title_el = MagicMock(); title_el.inner_text.return_value = role
    company_el = MagicMock(); company_el.inner_text.return_value = company
    loc_el = MagicMock(); loc_el.inner_text.return_value = location

    row = MagicMock()

    def _qs(selector):
        if "title" in selector:
            return title_el
        if "name" in selector:
            return company_el
        if "location" in selector:
            return loc_el
        return None

    row.query_selector.side_effect = _qs

    def _get_attr(name):
        if name in ("data-id", "id"):
            return job_id
        return None
    row.get_attribute.side_effect = _get_attr
    return row


class TestScrapeRemoteOK:
    def _run(self, rows):
        page = _mock_page(rows)
        with patch("scrape_jobs._playwright_browser", _playwright_ctx_mock(page)):
            return sj.scrape_remoteok(SAMPLE_CONFIG)

    def test_returns_only_matching_jobs(self):
        rows = [
            _remoteok_row("Senior SRE", "Acme", "1"),
            _remoteok_row("Frontend Developer", "Beta", "2"),
            _remoteok_row("Staff Platform Engineer", "Gamma", "3"),
        ]
        jobs = self._run(rows)
        titles = [j["role"] for j in jobs]
        assert "Senior SRE" in titles
        assert "Staff Platform Engineer" in titles
        assert "Frontend Developer" not in titles

    def test_constructs_url_from_data_id(self):
        rows = [_remoteok_row("SRE", "Co", "99999")]
        jobs = self._run(rows)
        assert "99999" in jobs[0]["url"]

    def test_defaults_empty_location_to_remote(self):
        rows = [_remoteok_row("SRE", "Co", "1", location="")]
        jobs = self._run(rows)
        assert jobs[0]["location"] == "Remote"

    def test_source_label(self):
        rows = [_remoteok_row("SRE", "Co", "1")]
        jobs = self._run(rows)
        assert jobs[0]["source"] == "RemoteOK"

    def test_returns_empty_when_no_rows(self):
        jobs = self._run([])
        assert jobs == []

    def test_deduplicates_same_url_within_run(self):
        rows = [
            _remoteok_row("SRE", "Acme", "42"),
            _remoteok_row("SRE", "Acme", "42"),  # same id → same URL
        ]
        jobs = self._run(rows)
        assert len(jobs) == 1

    def test_returns_empty_when_playwright_not_installed(self):
        with patch("scrape_jobs._has_playwright", return_value=False):
            jobs = sj.scrape_remoteok(SAMPLE_CONFIG)
        assert jobs == []


# ── WeWorkRemotely ────────────────────────────────────────────────────────────

def _wwr_item(role, company, href, *, is_category=False):
    """Build a mock li item for the WWR scraper."""
    title_el = MagicMock(); title_el.inner_text.return_value = role
    company_el = MagicMock(); company_el.inner_text.return_value = company
    link_el = MagicMock(); link_el.get_attribute.return_value = href

    item = MagicMock()

    def _qs(selector):
        if "category" in selector:
            return MagicMock() if is_category else None
        # Match various title selectors in order of preference
        if ".position" in selector or "span.title" in selector or selector == "h4":
            return title_el
        if "company" in selector:
            return company_el
        if "remote-jobs" in selector:
            return link_el
        return None

    item.query_selector.side_effect = _qs
    return item


class TestScrapeWeWorkRemotely:
    def _run(self, items, second_page=None):
        if second_page is not None:
            page = _mock_page(items, second_page)
        else:
            page = _mock_page(items, [])  # second call (ul.jobs fallback) returns empty
        with patch("scrape_jobs._playwright_browser", _playwright_ctx_mock(page)):
            return sj.scrape_weworkremotely(SAMPLE_CONFIG)

    def test_returns_only_matching_jobs(self):
        items = [
            _wwr_item("Senior SRE", "Acme", "/remote-jobs/1"),
            _wwr_item("Junior Frontend Developer", "Beta", "/remote-jobs/2"),
        ]
        jobs = self._run(items)
        assert len(jobs) == 1
        assert jobs[0]["role"] == "Senior SRE"

    def test_extracts_company_and_role(self):
        items = [_wwr_item("Senior SRE", "Acme", "/remote-jobs/1")]
        jobs = self._run(items)
        assert jobs[0]["company"] == "Acme"
        assert jobs[0]["role"] == "Senior SRE"

    def test_prepends_base_url_to_relative_href(self):
        items = [_wwr_item("Senior SRE", "Acme", "/remote-jobs/42")]
        jobs = self._run(items)
        assert jobs[0]["url"] == "https://weworkremotely.com/remote-jobs/42"

    def test_absolute_href_used_as_is(self):
        items = [_wwr_item("Senior SRE", "Acme", "https://weworkremotely.com/remote-jobs/42")]
        jobs = self._run(items)
        assert jobs[0]["url"] == "https://weworkremotely.com/remote-jobs/42"

    def test_source_label(self):
        items = [_wwr_item("SRE", "Co", "/remote-jobs/1")]
        jobs = self._run(items)
        assert jobs[0]["source"] == "We Work Remotely"

    def test_location_is_always_remote(self):
        items = [_wwr_item("SRE", "Co", "/remote-jobs/1")]
        jobs = self._run(items)
        assert jobs[0]["location"] == "Remote"

    def test_skips_category_items(self):
        items = [
            _wwr_item("DevOps / Sysadmin", "", "/remote-jobs/cat", is_category=True),
            _wwr_item("Senior SRE", "Acme", "/remote-jobs/1"),
        ]
        jobs = self._run(items)
        assert len(jobs) == 1

    def test_returns_empty_when_no_items(self):
        jobs = self._run([])
        assert jobs == []

    def test_returns_empty_when_playwright_not_installed(self):
        with patch("scrape_jobs._has_playwright", return_value=False):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert jobs == []


# ── HN scraper error handling ────────────────────────────────────────────────

class TestScrapeHnErrorHandling:
    def test_returns_empty_on_connection_error(self):
        with patch.object(sj.requests, "get", side_effect=requests.ConnectionError("unreachable")):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs == []


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
                    "linkedin": False,
                    "indeed": False,
                    "wellfound": False,
                    "apple": False,
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
        mock_ro = MagicMock(return_value=[{"url": "https://remoteok.io/1", "company": "A", "role": "SRE"}])
        mock_wwr = MagicMock(return_value=[])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            sj.run_all_scrapers(cfg)
        mock_ro.assert_called_once_with(cfg)
        mock_wwr.assert_not_called()

    def test_deduplicates_same_url_across_sources(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        dup_url = "https://shared.example.com/job"
        mock_ro = MagicMock(return_value=[{"url": dup_url, "company": "Acme", "role": "SRE"}])
        mock_wwr = MagicMock(return_value=[{"url": dup_url, "company": "Acme", "role": "SRE"}])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs, failed = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1
        assert failed == []

    def test_deduplicates_same_company_role_across_sources(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        mock_ro = MagicMock(return_value=[
            {"url": "https://remoteok.com/1", "company": "Acme", "role": "Senior SRE"}
        ])
        mock_wwr = MagicMock(return_value=[
            # Different URL, same company+role — should be deduped
            {"url": "https://weworkremotely.com/1", "company": "Acme", "role": "Senior SRE"}
        ])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs, failed = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1  # second occurrence dropped

    def test_continues_after_timeout(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        mock_ro = MagicMock(side_effect=requests.exceptions.Timeout())
        mock_wwr = MagicMock(return_value=[{"url": "https://b.com", "company": "B", "role": "SRE"}])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs, failed = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1
        assert failed == ["remoteok"]

    def test_continues_after_request_exception(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        mock_ro = MagicMock(side_effect=requests.exceptions.ConnectionError())
        mock_wwr = MagicMock(return_value=[{"url": "https://b.com", "company": "B", "role": "SRE"}])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs, failed = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1
        assert failed == ["remoteok"]

    def test_continues_after_unexpected_exception(self):
        cfg = self._config_with(remoteok=True, weworkremotely=True)
        mock_ro = MagicMock(side_effect=RuntimeError("boom"))
        mock_wwr = MagicMock(return_value=[{"url": "https://b.com", "company": "B", "role": "SRE"}])
        with patch.object(sj, "SCRAPERS", {"remoteok": mock_ro, "weworkremotely": mock_wwr}):
            jobs, failed = sj.run_all_scrapers(cfg)
        assert len(jobs) == 1
        assert failed == ["remoteok"]

    def test_all_disabled_returns_empty(self):
        jobs, failed = sj.run_all_scrapers(self._base_config)
        assert jobs == []
        assert failed == []
