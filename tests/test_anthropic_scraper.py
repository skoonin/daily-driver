"""Tests for scrape_anthropic()."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import scrape_jobs as sj

from fixtures import SAMPLE_CONFIG


def _playwright_ctx_mock(page):
    """Replace _playwright_browser with a context manager yielding page."""
    @contextmanager
    def _impl(*args, **kwargs):
        yield page
    return _impl


def _html_page(html: str) -> MagicMock:
    """Return a mock Playwright page whose content() returns the given HTML."""
    page = MagicMock()
    page.goto.return_value = None
    page.wait_for_load_state.return_value = None
    page.content.return_value = html
    return page


_CAREERS_HTML = """<html><body>
<a href="https://boards.greenhouse.io/anthropic/jobs/100001">
  <div class="jobRole"><p class="caption">Staff SRE</p></div>
  <div class="jobLocation"><p class="caption">Remote</p></div>
</a>
<a href="https://boards.greenhouse.io/anthropic/jobs/100002">
  <div class="jobRole"><p class="caption">Junior Frontend Engineer</p></div>
  <div class="jobLocation"><p class="caption">San Francisco, CA</p></div>
</a>
<a href="https://boards.greenhouse.io/anthropic/jobs/100003">
  <div class="jobRole"><p class="caption">Senior Platform Engineer</p></div>
  <div class="jobLocation"><p></p></div>
</a>
<a href="https://boards.greenhouse.io/anthropic/jobs/100001">
  <!-- duplicate of job 100001 — should be deduplicated -->
  <div class="jobRole"><p class="caption">Staff SRE</p></div>
  <div class="jobLocation"><p class="caption">Remote</p></div>
</a>
</body></html>"""

_EMPTY_HTML = "<html><body><p>No jobs</p></body></html>"


class TestScrapeAnthropic:
    def _run(self, html: str = _CAREERS_HTML):
        page = _html_page(html)
        with patch("scrape_jobs._playwright_browser", _playwright_ctx_mock(page)):
            return sj.scrape_anthropic(SAMPLE_CONFIG)

    def test_returns_matching_jobs(self):
        jobs = self._run()
        roles = [j["role"] for j in jobs]
        assert "Staff SRE" in roles

    def test_filters_non_matching_roles(self):
        jobs = self._run()
        roles = [j["role"] for j in jobs]
        assert "Junior Frontend Engineer" not in roles

    def test_deduplicates_same_url(self):
        jobs = self._run()
        urls = [j["url"] for j in jobs]
        assert len(urls) == len(set(urls))

    def test_company_is_anthropic(self):
        jobs = self._run()
        assert all(j["company"] == "Anthropic" for j in jobs)

    def test_source_label(self):
        jobs = self._run()
        assert all(j["source"] == "Anthropic Careers" for j in jobs)

    def test_location_extracted(self):
        jobs = self._run()
        sre_job = next(j for j in jobs if j["role"] == "Staff SRE")
        assert sre_job["location"] == "Remote"

    def test_defaults_empty_location_to_remote(self):
        jobs = self._run()
        platform = next(j for j in jobs if "Platform" in j["role"])
        assert platform["location"] == "Remote"

    def test_returns_empty_when_no_matching_jobs(self):
        jobs = self._run(_EMPTY_HTML)
        assert jobs == []

    def test_returns_empty_when_playwright_not_installed(self):
        with patch("scrape_jobs._has_playwright", return_value=False):
            jobs = sj.scrape_anthropic(SAMPLE_CONFIG)
        assert jobs == []
