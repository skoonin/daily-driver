"""Tests for scrape_hn_who_is_hiring()."""

from datetime import date
from unittest.mock import MagicMock, patch

import scrape_jobs as sj

from fixtures import SAMPLE_CONFIG


def _make_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.text = text
    return mock


# Minimal index page HTML with a matching "Who is hiring?" link
def _index_html(month_str: str) -> str:
    return f"""<html><body>
<a href="item?id=42000001">Ask HN: Who is hiring? ({month_str})</a>
</body></html>"""


# HN thread HTML with comment rows
_THREAD_HTML = """<html><body>
<table>
  <tr class="athing comtr" id="99000001">
    <td class="ind"><img width="0"/></td>
    <td><div class="comment">
      <div class="commtext">Acme Corp | Senior SRE | Remote | Full-time
        <p>More details here.</p>
      </div>
    </div></td>
  </tr>
  <tr class="athing comtr" id="99000002">
    <td class="ind"><img width="0"/></td>
    <td><div class="comment">
      <div class="commtext">BetaCo | Junior Frontend Dev | New York
      </div>
    </div></td>
  </tr>
  <tr class="athing comtr" id="99000003">
    <td class="ind"><img width="40"/></td>
    <td><div class="comment">
      <div class="commtext">Nested comment about SRE -- should be skipped
      </div>
    </div></td>
  </tr>
  <tr class="athing comtr" id="99000004">
    <td class="ind"><img width="0"/></td>
    <td><div class="comment">
      <div class="commtext">GammaCo | Staff Platform Engineer | San Francisco | Contract
      </div>
    </div></td>
  </tr>
</table>
</body></html>"""

# Thread with a single entry but no pipe-separated parts (should be skipped)
_THREAD_SHORT_PARTS = """<html><body>
<table>
  <tr class="athing comtr" id="88000001">
    <td class="ind"><img width="0"/></td>
    <td><div class="comment">
      <div class="commtext">Just a comment with no pipe separators
      </div>
    </div></td>
  </tr>
</table>
</body></html>"""


class TestScrapeHNWhoIsHiring:
    def _month_str(self) -> str:
        # Both this and the production code call date.today() independently;
        # tests will fail if a run straddles a month boundary (acceptable for a personal tool).
        return date.today().strftime("%B %Y")

    def _two_responses(self, thread_html: str = _THREAD_HTML):
        return [
            _make_response(_index_html(self._month_str())),
            _make_response(thread_html),
        ]

    def test_returns_matching_jobs(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        roles = [j["role"] for j in jobs]
        assert "Senior SRE" in roles

    def test_skips_non_matching_roles(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        companies = [j["company"] for j in jobs]
        assert "BetaCo" not in companies

    def test_skips_nested_comments(self):
        # The row with img width=40 should be skipped regardless of content
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        # Only top-level comments (width=0) are included
        for job in jobs:
            assert "Nested" not in job.get("company", "")

    def test_parses_company_from_first_part(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs[0]["company"] == "Acme Corp"

    def test_parses_role_from_second_part(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs[0]["role"] == "Senior SRE"

    def test_detects_remote_location(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs[0]["location"] == "Remote"

    def test_uses_city_location_when_not_remote(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        gamma = next(j for j in jobs if j["company"] == "GammaCo")
        assert gamma["location"] == "San Francisco"

    def test_source_label(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert all(j["source"] == "HN Who's Hiring" for j in jobs)

    def test_url_contains_comment_id(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert "99000001" in jobs[0]["url"]

    def test_returns_empty_when_thread_not_found(self):
        # Index page with no matching link
        stale_index = _make_response("<html><body><a href='item?id=1'>Old thread</a></body></html>")
        with patch("scrape_jobs.requests.get", return_value=stale_index):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs == []

    def test_skips_rows_without_enough_pipe_parts(self):
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses(_THREAD_SHORT_PARTS)):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs == []

    def test_respects_max_posts_limit(self):
        cfg = {
            "job_search": {
                "roles": ["SRE", "Platform Engineer"],
                "scraper": {
                    "enabled": True,
                    "timeout": 5,
                    "hn_max_posts": 1,
                },
            },
        }
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(cfg)
        assert len(jobs) == 1

    def test_handles_absolute_url_in_index_href(self):
        index = f"""<html><body>
<a href="https://news.ycombinator.com/item?id=42000001">Ask HN: Who is hiring? ({self._month_str()})</a>
</body></html>"""
        responses = [_make_response(index), _make_response(_THREAD_HTML)]
        with patch("scrape_jobs.requests.get", side_effect=responses):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert len(jobs) >= 1

    def test_skips_row_without_comment_div(self):
        thread = """<html><body><table>
  <tr class="athing comtr" id="99000001">
    <td class="ind"><img width="0"/></td>
    <td><span>no comment div here</span></td>
  </tr>
</table></body></html>"""
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses(thread)):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs == []

    def test_skips_row_without_commtext_div(self):
        thread = """<html><body><table>
  <tr class="athing comtr" id="99000001">
    <td class="ind"><img width="0"/></td>
    <td><div class="comment"><span>no commtext div</span></div></td>
  </tr>
</table></body></html>"""
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses(thread)):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs == []

    def test_falls_back_to_thread_url_when_no_comment_id(self):
        thread = """<html><body><table>
  <tr class="athing comtr">
    <td class="ind"><img width="0"/></td>
    <td><div class="comment">
      <div class="commtext">Acme Corp | Senior SRE | Remote</div>
    </div></td>
  </tr>
</table></body></html>"""
        with patch("scrape_jobs.requests.get", side_effect=self._two_responses(thread)):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert len(jobs) == 1
        assert "item?id=42000001" in jobs[0]["url"]
