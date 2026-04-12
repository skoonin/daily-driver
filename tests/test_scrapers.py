"""Tests for scrapers and run_all_scrapers() orchestrator."""

import copy
import csv
import json
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
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

def _remoteok_api_item(position, company, job_id, location="", url=""):
    """Build a dict matching RemoteOK's /api JSON shape."""
    return {
        "id": str(job_id),
        "position": position,
        "company": company,
        "location": location,
        "url": url or f"https://remoteok.com/remote-jobs/{job_id}",
        "tags": [],
    }


def _mock_response_json(data, status_code=200):
    """Build a mock requests.Response that returns data from .json()."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.ok = status_code < 400
    return resp


class TestScrapeRemoteOK:
    def _run(self, api_items):
        resp = _mock_response_json([{"legal": "..."}] + api_items)
        with patch("scrape_jobs._api_get", return_value=resp):
            return sj.scrape_remoteok(SAMPLE_CONFIG)

    def test_returns_only_matching_jobs(self):
        items = [
            _remoteok_api_item("Senior SRE", "Acme", "1"),
            _remoteok_api_item("Frontend Developer", "Beta", "2"),
            _remoteok_api_item("Staff Platform Engineer", "Gamma", "3"),
        ]
        jobs = self._run(items)
        titles = [j["role"] for j in jobs]
        assert "Senior SRE" in titles
        assert "Staff Platform Engineer" in titles
        assert "Frontend Developer" not in titles

    def test_preserves_url_from_api(self):
        items = [_remoteok_api_item("SRE", "Co", "99999")]
        jobs = self._run(items)
        assert "99999" in jobs[0]["url"]

    def test_defaults_empty_location_to_remote(self):
        items = [_remoteok_api_item("SRE", "Co", "1", location="")]
        jobs = self._run(items)
        assert jobs[0]["location"] == "Remote"

    def test_source_label(self):
        items = [_remoteok_api_item("SRE", "Co", "1")]
        jobs = self._run(items)
        assert jobs[0]["source"] == "RemoteOK"

    def test_returns_empty_when_no_items(self):
        jobs = self._run([])
        assert jobs == []

    def test_deduplicates_same_id_within_run(self):
        items = [
            _remoteok_api_item("SRE", "Acme", "42"),
            _remoteok_api_item("SRE", "Acme", "42"),
        ]
        jobs = self._run(items)
        assert len(jobs) == 1

    def test_returns_empty_when_api_fails(self):
        with patch("scrape_jobs._api_get", return_value=None):
            jobs = sj.scrape_remoteok(SAMPLE_CONFIG)
        assert jobs == []


# ── WeWorkRemotely ────────────────────────────────────────────────────────────

def _wwr_rss_xml(items):
    """Build RSS XML bytes with <item> elements.

    Each item is a dict with keys: title, link, region (optional).
    """
    parts = ['<?xml version="1.0" encoding="UTF-8"?><rss><channel>']
    for it in items:
        parts.append("<item>")
        parts.append(f"<title>{it['title']}</title>")
        parts.append(f"<link>{it['link']}</link>")
        if "region" in it:
            parts.append(f"<region>{it['region']}</region>")
        parts.append("</item>")
    parts.append("</channel></rss>")
    return "".join(parts).encode()


def _mock_response_content(content_bytes, status_code=200):
    """Build a mock requests.Response with .content for XML parsing."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.content = content_bytes
    resp.ok = status_code < 400
    return resp


class TestScrapeWeWorkRemotely:
    def _run(self, rss_items):
        """Run scrape_weworkremotely with _api_get returning the same RSS for every category."""
        resp = _mock_response_content(_wwr_rss_xml(rss_items))
        with patch("scrape_jobs._api_get", return_value=resp):
            return sj.scrape_weworkremotely(SAMPLE_CONFIG)

    def test_returns_only_matching_jobs(self):
        items = [
            {"title": "Acme: Senior SRE", "link": "https://weworkremotely.com/1"},
            {"title": "Beta: Junior Frontend Developer", "link": "https://weworkremotely.com/2"},
        ]
        jobs = self._run(items)
        titles = [j["role"] for j in jobs]
        assert "Senior SRE" in titles
        assert "Junior Frontend Developer" not in titles

    def test_extracts_company_and_role(self):
        items = [{"title": "Acme: Senior SRE", "link": "https://weworkremotely.com/1"}]
        jobs = self._run(items)
        assert jobs[0]["company"] == "Acme"
        assert jobs[0]["role"] == "Senior SRE"

    def test_title_without_colon_uses_whole_as_role(self):
        items = [{"title": "Senior SRE", "link": "https://weworkremotely.com/1"}]
        jobs = self._run(items)
        assert jobs[0]["company"] == ""
        assert jobs[0]["role"] == "Senior SRE"

    def test_source_label(self):
        items = [{"title": "Co: SRE", "link": "https://weworkremotely.com/1"}]
        jobs = self._run(items)
        assert jobs[0]["source"] == "We Work Remotely"

    def test_uses_region_element_for_location(self):
        items = [{"title": "Co: SRE", "link": "https://weworkremotely.com/1", "region": "Europe"}]
        jobs = self._run(items)
        assert jobs[0]["location"] == "Europe"

    def test_defaults_missing_region_to_remote(self):
        items = [{"title": "Co: SRE", "link": "https://weworkremotely.com/1"}]
        jobs = self._run(items)
        assert jobs[0]["location"] == "Remote"

    def test_deduplicates_same_url_across_categories(self):
        items = [
            {"title": "Co: SRE", "link": "https://weworkremotely.com/same"},
            {"title": "Co: SRE", "link": "https://weworkremotely.com/same"},
        ]
        jobs = self._run(items)
        # Same RSS returned for each of 3 categories, but URL dedup means only 1
        assert len(jobs) == 1

    def test_returns_empty_when_no_items(self):
        jobs = self._run([])
        assert jobs == []

    def test_returns_empty_when_api_fails(self):
        with patch("scrape_jobs._api_get", return_value=None):
            jobs = sj.scrape_weworkremotely(SAMPLE_CONFIG)
        assert jobs == []


# ── HN scraper error handling ────────────────────────────────────────────────

class TestScrapeHnErrorHandling:
    def test_returns_empty_when_api_fails(self):
        with patch("scrape_jobs._api_get", return_value=None):
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
                    "greenhouse": False,
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
        # Orchestrator passes a phase-config copy (with headless overridden)
        # rather than the original cfg, so assert on call count + sources.
        mock_ro.assert_called_once()
        passed_cfg = mock_ro.call_args.args[0]
        assert passed_cfg["job_search"]["scraper"]["sources"]["remoteok"] is True
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


# ── Parallel phase split ──────────────────────────────────────────────────────

class TestRunAllScrapersParallel:
    _base_config = {
        "job_search": {
            "roles": ["SRE"],
            "scraper": {
                "enabled": True,
                "timeout": 5,
                "headless": False,
                "parallel_workers": 4,
                "sources": {
                    "remoteok": True,
                    "weworkremotely": True,
                    "hn_who_is_hiring": True,
                    "greenhouse": True,
                    "apple": True,
                    "linkedin": True,
                    "indeed": True,
                    "wellfound": True,
                },
            },
        },
    }

    def _config(self, *, workers: int = 4) -> dict:
        cfg = copy.deepcopy(self._base_config)
        cfg["job_search"]["scraper"]["parallel_workers"] = workers
        return cfg

    def test_phase_split_headless_flag_and_call_count(self):
        """All 8 scrapers called once; headless sources see headless=True,
        non-headless sources see headless=False."""
        seen_modes: dict[str, bool] = {}

        def _make_mock(sid: str):
            def _fn(cfg):
                seen_modes[sid] = sj.scraper_cfg(cfg).get("headless")
                return [{
                    "url": f"https://example.com/{sid}",
                    "company": sid,
                    "role": "SRE",
                }]
            return MagicMock(side_effect=_fn)

        mocks = {sid: _make_mock(sid) for sid in sj.SCRAPERS}
        with patch.object(sj, "SCRAPERS", mocks):
            jobs, failed = sj.run_all_scrapers(self._config())

        # Every scraper invoked exactly once
        for sid, m in mocks.items():
            assert m.call_count == 1, f"{sid} called {m.call_count} times"

        # Phase 1 sources saw headless=True
        for sid in ("remoteok", "weworkremotely", "hn_who_is_hiring", "greenhouse", "apple"):
            assert seen_modes[sid] is True, f"{sid} should run headless"

        # Phase 2 sources saw headless=False
        for sid in ("linkedin", "indeed", "wellfound"):
            assert seen_modes[sid] is False, f"{sid} should run non-headless"

        # All jobs merged; one row per source, all unique
        assert len(jobs) == 8
        assert failed == []

    def test_parallel_speedup(self):
        """With parallel_workers=4 and only 4 headless-safe sources each sleeping
        0.2s, total wall-clock should be well under the 0.8s serial equivalent."""
        def _slow(cfg):
            time.sleep(0.2)
            return []

        # Enable 4 headless-safe sources only
        cfg = self._config(workers=4)
        for sid in sj.SCRAPERS:
            cfg["job_search"]["scraper"]["sources"][sid] = sid in {
                "remoteok", "weworkremotely", "hn_who_is_hiring", "greenhouse"
            }

        mocks = {sid: MagicMock(side_effect=_slow) for sid in sj.SCRAPERS}
        start = time.perf_counter()
        with patch.object(sj, "SCRAPERS", mocks):
            sj.run_all_scrapers(cfg)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"parallel should be <0.5s, got {elapsed:.2f}s"

    def test_workers_1_is_serial(self):
        """parallel_workers=1 forces Phase 1 to run one-at-a-time; 4 sources
        sleeping 0.2s each should take at least 0.7s."""
        def _slow(cfg):
            time.sleep(0.2)
            return []

        cfg = self._config(workers=1)
        for sid in sj.SCRAPERS:
            cfg["job_search"]["scraper"]["sources"][sid] = sid in {
                "remoteok", "weworkremotely", "hn_who_is_hiring", "greenhouse"
            }

        mocks = {sid: MagicMock(side_effect=_slow) for sid in sj.SCRAPERS}
        start = time.perf_counter()
        with patch.object(sj, "SCRAPERS", mocks):
            sj.run_all_scrapers(cfg)
        elapsed = time.perf_counter() - start
        assert elapsed >= 0.7, f"serial should be >=0.7s, got {elapsed:.2f}s"

    def test_exception_in_one_phase1_scraper(self):
        """One Phase 1 scraper raising Timeout must not block the others; its
        source id lands in failed_sources, others still produce jobs."""
        def _ok(sid):
            return lambda cfg: [{
                "url": f"https://example.com/{sid}",
                "company": sid,
                "role": "SRE",
            }]

        cfg = self._config()
        # Only Phase 1 sources enabled so we're exercising the parallel path
        for sid in sj.SCRAPERS:
            cfg["job_search"]["scraper"]["sources"][sid] = sid not in sj.NON_HEADLESS_SOURCES

        mocks = {sid: MagicMock(side_effect=_ok(sid)) for sid in sj.SCRAPERS}
        mocks["remoteok"] = MagicMock(side_effect=requests.exceptions.Timeout())

        with patch.object(sj, "SCRAPERS", mocks):
            jobs, failed = sj.run_all_scrapers(cfg)

        assert "remoteok" in failed
        returned_sources = {j["company"] for j in jobs}
        # The four other Phase 1 sources all produced their row
        assert returned_sources == {"weworkremotely", "hn_who_is_hiring", "greenhouse", "apple"}


# ── Country helpers ───────────────────────────────────────────────────────────

def test_countries_list_default():
    assert sj.countries_list({}) == ["US", "CA"]


def test_countries_list_from_config():
    cfg = {"job_search": {"scraper": {"countries": ["US", "GB"]}}}
    assert sj.countries_list(cfg) == ["US", "GB"]


def test_country_params_known():
    assert sj.country_params("US")["apple_locale"] == "en-us"
    assert sj.country_params("ca")["linkedin_location"] == "Canada"


def test_country_params_unknown_returns_empty(caplog):
    with caplog.at_level("WARNING"):
        assert sj.country_params("XX") == {}
    assert "unknown country code" in caplog.text.lower()


# ── Multi-country scraper iteration ──────────────────────────────────────────


def _apple_mock_page():
    """Build a mock Playwright page for Apple's response-interception scraper.

    The page simulates:
      - goto() records visited URLs
      - query_selector("input.search-typeahead-input") returns a mock input
      - on("response", cb) / remove_listener("response", cb) are no-ops
    """
    page = MagicMock()
    page._visited = []
    page.goto.side_effect = lambda url, **kw: page._visited.append(url) or None
    page.wait_for_timeout.return_value = None

    search_input = MagicMock()
    search_input.click.return_value = None
    search_input.fill.return_value = None
    search_input.press.return_value = None
    page.query_selector.return_value = search_input

    page.on.return_value = None
    page.remove_listener.return_value = None
    return page


class TestScrapeAppleMultiCountry:
    def test_visits_every_configured_country_locale(self):
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["job_search"]["scraper"]["countries"] = ["US", "CA"]
        page = _apple_mock_page()
        with patch("scrape_jobs._playwright_browser", _playwright_ctx_mock(page)):
            sj.scrape_apple(cfg)
        visited = page._visited
        assert any("/en-us/search" in u for u in visited)
        assert any("/en-ca/search" in u for u in visited)

    def test_default_countries_when_unset(self):
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["job_search"]["scraper"].pop("countries", None)
        page = _apple_mock_page()
        with patch("scrape_jobs._playwright_browser", _playwright_ctx_mock(page)):
            sj.scrape_apple(cfg)
        visited = page._visited
        assert any("/en-us/search" in u for u in visited)
        assert any("/en-ca/search" in u for u in visited)


class TestScrapeLinkedInMultiCountry:
    def test_visits_every_configured_country(self):
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["job_search"]["scraper"]["countries"] = ["US", "CA"]
        visited: list[str] = []
        page = _mock_page([])
        page.goto.side_effect = lambda url, **kw: visited.append(url) or None
        with patch("scrape_jobs._playwright_browser", _playwright_ctx_mock(page)):
            sj.scrape_linkedin(cfg)
        assert any("location=United+States" in u for u in visited)
        assert any("location=Canada" in u for u in visited)

    def test_linkedin_url_uses_7day_window_no_remote_filter(self):
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        visited: list[str] = []
        page = _mock_page([])
        page.goto.side_effect = lambda url, **kw: visited.append(url) or None
        with patch("scrape_jobs._playwright_browser", _playwright_ctx_mock(page)):
            sj.scrape_linkedin(cfg)
        for u in visited:
            assert "f_TPR=r604800" in u
            assert "f_WT=2" not in u


class TestScrapeIndeedMultiCountry:
    def test_uses_configured_regional_host(self):
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["job_search"]["scraper"]["countries"] = ["US", "CA"]
        visited: list[str] = []
        page = _mock_page([])
        page.goto.side_effect = lambda url, **kw: visited.append(url) or None
        with patch("scrape_jobs._playwright_browser", _playwright_ctx_mock(page)):
            sj.scrape_indeed(cfg)
        assert any("www.indeed.com" in u for u in visited)
        assert any("ca.indeed.com" in u for u in visited)


# ── enrich_company_descriptions budget fix ──────────────────────────────────

class TestEnrichCompanyBudgetFix:
    """After budget exhaustion, cached companies must still get enriched."""

    def test_cached_companies_applied_after_budget(self):
        """Two companies, budget=1. First company gets a Claude call; second
        doesn't (budget hit). But if the first company appears again later
        in the list, it should still get its cached product applied."""
        jobs = [
            {"company": "Acme", "role": "SRE"},
            {"company": "Beta", "role": "SRE"},
            {"company": "Acme", "role": "Staff SRE"},
        ]
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["job_search"]["scraper"]["max_enrich_companies"] = 1

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Acme builds rockets\n4.2\n"

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run", return_value=mock_result):
            sj.enrich_company_descriptions(jobs, cfg)

        # First Acme job enriched via Claude call
        assert jobs[0]["product"] == "Acme builds rockets"
        assert jobs[0]["gd_rating"] == "4.2"
        # Beta skipped (budget), product stays empty
        assert not jobs[1].get("product")
        # Second Acme job enriched via cache (the old break bug would skip this)
        assert jobs[2]["product"] == "Acme builds rockets"
        assert jobs[2]["gd_rating"] == "4.2"


# ── Backfill ────────────────────────────────────────────────────────────────

class TestBackfillHelpers:
    def test_row_to_dict_maps_columns(self):
        row = {
            "Status": "found", "Company": "Acme", "Product/Purpose": "Widgets",
            "Role": "SRE", "Comp": "$100k", "Location": "Remote",
            "Fit": "7/10", "GD Rating": "4.1", "Source": "LinkedIn",
            "Date Found": "2026-04-12", "Date Applied": "", "Link": "https://example.com",
            "Notes": "Good role",
        }
        d = sj._row_to_dict(row)
        assert d["company"] == "Acme"
        assert d["product"] == "Widgets"
        assert d["url"] == "https://example.com"
        assert d["fit"] == "7/10"

    def test_row_to_dict_clears_placeholder_product(self):
        row = {"Product/Purpose": "(auto-scraped -- needs fill)", "Company": "X"}
        d = sj._row_to_dict(row)
        assert d["product"] == ""

    def test_dict_to_row_roundtrips(self):
        job = {
            "status": "found", "company": "Acme", "product": "Widgets",
            "role": "SRE", "comp": "", "location": "Remote",
            "fit": "8/10", "gd_rating": "4.0", "source": "HN",
            "date_found": "2026-04-12", "date_applied": "", "url": "https://x.com",
            "notes": "test",
        }
        row = sj._dict_to_row(job, sj.CANONICAL_HEADER)
        assert row["Company"] == "Acme"
        assert row["Fit"] == "8/10"
        assert row["Link"] == "https://x.com"

    def test_backfill_writes_enriched_data(self):
        """backfill() reads CSV, calls enrichers, writes back updated rows."""
        header = sj.CANONICAL_HEADER
        rows = [
            {"Status": "found", "Company": "Acme", "Product/Purpose": "",
             "Role": "SRE", "Comp": "", "Location": "Remote",
             "Fit": "", "GD Rating": "", "Source": "HN",
             "Date Found": "2026-04-12", "Date Applied": "",
             "Link": "https://example.com/1", "Notes": ""},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "jobs.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()
                writer.writerows(rows)

            cfg = copy.deepcopy(SAMPLE_CONFIG)

            # Mock all three enrichers to set values
            def mock_company(jobs, config):
                for j in jobs:
                    if not j.get("product"):
                        j["product"] = "Acme builds widgets"
                        j["gd_rating"] = "4.5"

            def mock_fit(jobs, config):
                for j in jobs:
                    if not j.get("fit"):
                        j["fit"] = "8/10"

            def mock_notes(jobs, config):
                for j in jobs:
                    if not j.get("notes"):
                        j["notes"] = "Looks good"

            with patch.object(sj, "enrich_company_descriptions", side_effect=mock_company), \
                 patch.object(sj, "enrich_fit", side_effect=mock_fit), \
                 patch.object(sj, "enrich_notes", side_effect=mock_notes):
                sj.backfill(cfg, csv_path)

            # Read back and verify
            with open(csv_path, newline="") as f:
                result = list(csv.DictReader(f))

            assert len(result) == 1
            assert result[0]["Product/Purpose"] == "Acme builds widgets"
            assert result[0]["GD Rating"] == "4.5"
            assert result[0]["Fit"] == "8/10"
            assert result[0]["Notes"] == "Looks good"
            # Preserved fields
            assert result[0]["Company"] == "Acme"
            assert result[0]["Status"] == "found"
            assert result[0]["Link"] == "https://example.com/1"

    def test_backfill_preserves_existing_data(self):
        """backfill() must not clobber manually-entered values."""
        header = sj.CANONICAL_HEADER
        rows = [
            {"Status": "applied", "Company": "Rootly", "Product/Purpose": "Incident mgmt",
             "Role": "SRE", "Comp": "$150k", "Location": "Toronto",
             "Fit": "7/10", "GD Rating": "3.9", "Source": "HN",
             "Date Found": "2026-04-06", "Date Applied": "2026-04-07",
             "Link": "https://example.com/2", "Notes": "Applied via referral"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "jobs.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()
                writer.writerows(rows)

            cfg = copy.deepcopy(SAMPLE_CONFIG)

            # Enrichers should be called but skip already-filled fields
            with patch.object(sj, "enrich_company_descriptions") as m_co, \
                 patch.object(sj, "enrich_fit") as m_fit, \
                 patch.object(sj, "enrich_notes") as m_notes:
                sj.backfill(cfg, csv_path)

            with open(csv_path, newline="") as f:
                result = list(csv.DictReader(f))

            assert result[0]["Product/Purpose"] == "Incident mgmt"
            assert result[0]["Fit"] == "7/10"
            assert result[0]["Date Applied"] == "2026-04-07"
            assert result[0]["Notes"] == "Applied via referral"
